"""Add private Copywriter inputs without changing existing Editor contracts or grants."""

from alembic import op

revision = "0014_copywriter"
down_revision = "0013_editor_triage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE ai_inputs ADD COLUMN copy_context json,
        DROP CONSTRAINT ai_input_editor_pair,
        ADD CONSTRAINT ai_input_content_pair CHECK ((post_id IS NULL) = (revision_id IS NULL)
            AND ((post_id IS NULL AND editor_context IS NULL AND copy_context IS NULL)
            OR (post_id IS NOT NULL AND ((editor_context IS NULL) <> (copy_context IS NULL)))))""")
    op.execute("""CREATE FUNCTION public.smm_copy_input_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ DECLARE r public.ai_runs%ROWTYPE; BEGIN
        SELECT * INTO r FROM public.ai_runs WHERE id=NEW.run_id AND workspace_id=NEW.workspace_id;
        IF r.profile='copywriter' THEN
            IF NEW.copy_context IS NULL OR NEW.editor_context IS NOT NULL
              OR NEW.citations::jsonb<>'[]'::jsonb OR NEW.actor_id<>r.actor_id OR NOT EXISTS (
                SELECT 1 FROM public.posts p JOIN public.post_revisions v
                  ON v.post_id=p.id AND v.workspace_id=p.workspace_id
                WHERE p.workspace_id=NEW.workspace_id AND p.id=NEW.post_id AND p.brand_id=r.brand_id
                  AND v.id=NEW.revision_id AND p.current_revision_id=v.id
                  AND NEW.copy_context->>'contract'='copywriting-context-v1'
                  AND NEW.copy_context->'source'->>'post_id'=p.id::text
                  AND NEW.copy_context->'source'->>'brand_id'=p.brand_id::text
                  AND NEW.copy_context->'source'->'revision'->>'id'=v.id::text
                  AND NEW.copy_context->'source'->'revision'->>'content_hash'=v.content_hash)
                THEN RAISE EXCEPTION 'copywriter_input_required'; END IF;
        ELSIF NEW.copy_context IS NOT NULL THEN RAISE EXCEPTION 'unexpected_copywriter_input'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_copy_input_guard() FROM PUBLIC")
    op.execute("CREATE TRIGGER copy_input_guard BEFORE INSERT ON ai_inputs FOR EACH ROW EXECUTE FUNCTION public.smm_copy_input_guard()")
    op.execute("""CREATE FUNCTION public.smm_copy_run_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.profile='copywriter' AND NEW.state IN ('running','needs_review') AND NOT EXISTS (
            SELECT 1 FROM public.ai_inputs i JOIN public.posts p
              ON p.id=i.post_id AND p.workspace_id=i.workspace_id
            WHERE i.workspace_id=NEW.workspace_id AND i.run_id=NEW.id
              AND i.actor_id=NEW.actor_id AND i.copy_context IS NOT NULL
              AND p.current_revision_id=i.revision_id AND p.brand_id=NEW.brand_id)
            THEN RAISE EXCEPTION 'copywriter_current_input_required'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_copy_run_guard() FROM PUBLIC")
    op.execute("CREATE TRIGGER copy_run_guard BEFORE UPDATE ON ai_runs FOR EACH ROW EXECUTE FUNCTION public.smm_copy_run_guard()")


def downgrade() -> None:
    # Fail before dropping anything: never silently destroy a saved input's provenance.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM ai_inputs WHERE copy_context IS NOT NULL)
        THEN RAISE EXCEPTION 'copywriter_history_requires_restore_plan'; END IF; END $$""")
    op.execute("DROP TRIGGER copy_run_guard ON ai_runs")
    op.execute("DROP FUNCTION public.smm_copy_run_guard()")
    op.execute("DROP TRIGGER copy_input_guard ON ai_inputs")
    op.execute("DROP FUNCTION public.smm_copy_input_guard()")
    op.execute("""ALTER TABLE ai_inputs DROP CONSTRAINT ai_input_content_pair,
        DROP COLUMN copy_context,
        ADD CONSTRAINT ai_input_editor_pair CHECK ((post_id IS NULL) = (revision_id IS NULL)
            AND (post_id IS NULL) = (editor_context IS NULL))""")
