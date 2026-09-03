"""Text-only editorial input bindings. No automatic selection, content writes or provider enablement."""

from alembic import op

revision = "0012_editor_review"
down_revision = "0011_profile_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE ai_inputs ADD COLUMN post_id uuid, ADD COLUMN revision_id uuid,
        ADD COLUMN editor_context json,
        ADD CONSTRAINT fk_ai_input_editor_revision FOREIGN KEY(workspace_id,post_id,revision_id)
            REFERENCES post_revisions(workspace_id,post_id,id),
        ADD CONSTRAINT ai_input_editor_pair CHECK ((post_id IS NULL) = (revision_id IS NULL)
            AND (post_id IS NULL) = (editor_context IS NULL))""")
    op.execute("GRANT SELECT ON posts,post_revisions,content_records,file_metadata TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_editor_input_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ DECLARE r public.ai_runs%ROWTYPE; BEGIN
        SELECT * INTO r FROM public.ai_runs WHERE id=NEW.run_id AND workspace_id=NEW.workspace_id;
        IF r.profile='editor' THEN
            IF NEW.editor_context IS NULL OR NEW.citations::jsonb<>'[]'::jsonb OR NOT EXISTS (
                SELECT 1 FROM public.posts p JOIN public.post_revisions v ON v.post_id=p.id
                WHERE p.workspace_id=NEW.workspace_id AND p.id=NEW.post_id AND p.brand_id=r.brand_id
                  AND v.id=NEW.revision_id AND p.current_revision_id=v.id
                  AND NEW.editor_context->>'post_id'=p.id::text
                  AND NEW.editor_context->>'brand_id'=p.brand_id::text
                  AND NEW.editor_context->'revision'->>'id'=v.id::text
                  AND NEW.editor_context->'revision'->>'content_hash'=v.content_hash)
                THEN RAISE EXCEPTION 'editor_input_required'; END IF;
        ELSIF NEW.editor_context IS NOT NULL THEN RAISE EXCEPTION 'unexpected_editor_input'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_editor_input_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER editor_input_guard BEFORE INSERT ON ai_inputs FOR EACH ROW EXECUTE FUNCTION public.smm_editor_input_guard()"
    )
    op.execute("""CREATE FUNCTION public.smm_editor_run_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.profile='editor' AND NEW.state IN ('running','needs_review') AND NOT EXISTS (
            SELECT 1 FROM public.ai_inputs i JOIN public.posts p ON p.id=i.post_id
            WHERE i.workspace_id=NEW.workspace_id AND i.run_id=NEW.id
              AND i.actor_id=NEW.actor_id AND i.editor_context IS NOT NULL
              AND p.current_revision_id=i.revision_id AND p.brand_id=NEW.brand_id)
            THEN RAISE EXCEPTION 'editor_current_input_required'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_editor_run_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER editor_run_guard BEFORE UPDATE ON ai_runs FOR EACH ROW EXECUTE FUNCTION public.smm_editor_run_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER editor_run_guard ON ai_runs")
    op.execute("DROP FUNCTION public.smm_editor_run_guard()")
    op.execute("DROP TRIGGER editor_input_guard ON ai_inputs")
    op.execute("DROP FUNCTION public.smm_editor_input_guard()")
    op.execute(
        "REVOKE SELECT ON posts,post_revisions,content_records,file_metadata FROM smm_worker"
    )
    op.execute(
        "ALTER TABLE ai_inputs DROP CONSTRAINT fk_ai_input_editor_revision, DROP CONSTRAINT ai_input_editor_pair, DROP COLUMN editor_context, DROP COLUMN revision_id, DROP COLUMN post_id"
    )
