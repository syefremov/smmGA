"""Private immutable planning inputs; no content writes or automatic profile activation."""

from alembic import op

revision = "0016_planner"
down_revision = "0015_copy_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE ai_inputs ADD COLUMN plan_id uuid, ADD COLUMN planner_context json,
        ADD CONSTRAINT fk_ai_input_plan FOREIGN KEY(workspace_id,plan_id) REFERENCES content_records(workspace_id,id),
        ADD CONSTRAINT ai_input_planner_pair CHECK ((plan_id IS NULL)=(planner_context IS NULL)
            AND (plan_id IS NULL OR (post_id IS NULL AND revision_id IS NULL AND editor_context IS NULL AND copy_context IS NULL)))""")
    # Bounded, existing tenant-aware boolean check, not SELECT on other employees' identities.
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_assignable_member(uuid,uuid) TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_planner_input_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ DECLARE r public.ai_runs%ROWTYPE; BEGIN
        SELECT * INTO r FROM public.ai_runs WHERE id=NEW.run_id AND workspace_id=NEW.workspace_id;
        IF r.profile='content_planner' THEN
            IF NEW.planner_context IS NULL OR NEW.citations::jsonb<>'[]'::jsonb OR NEW.actor_id<>r.actor_id
              OR NOT EXISTS (
                SELECT 1 FROM public.content_records p JOIN public.content_records c
                  ON c.workspace_id=p.workspace_id AND c.id=(p.body->>'campaign_id')::uuid
                WHERE p.workspace_id=NEW.workspace_id AND p.id=NEW.plan_id AND p.kind='content_plan'
                  AND p.brand_id=r.brand_id AND c.brand_id=p.brand_id AND c.kind='campaign'
                  AND p.expires_at>now() AND c.expires_at>now()
                  AND NOT EXISTS (SELECT 1 FROM public.content_records n WHERE n.workspace_id=p.workspace_id
                    AND ((n.family_id=p.family_id AND n.number>p.number) OR (n.family_id=c.family_id AND n.number>c.number)))
                  AND NEW.planner_context->>'contract'='planning-context-v1'
                  AND NEW.planner_context->>'brand_id'=r.brand_id::text
                  AND NEW.planner_context->'plan'->>'id'=p.id::text
                  AND NEW.planner_context->'plan'->>'content_hash'=p.content_hash
                  AND NEW.planner_context->'campaign'->>'id'=c.id::text
                  AND NEW.planner_context->'campaign'->>'content_hash'=c.content_hash
                  AND NEW.planner_context->>'direction'=NEW.question)
              THEN RAISE EXCEPTION 'planner_input_required'; END IF;
        ELSIF NEW.planner_context IS NOT NULL OR NEW.plan_id IS NOT NULL
            THEN RAISE EXCEPTION 'unexpected_planner_input'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_planner_input_guard() FROM PUBLIC")
    op.execute("CREATE TRIGGER planner_input_guard BEFORE INSERT ON ai_inputs FOR EACH ROW EXECUTE FUNCTION public.smm_planner_input_guard()")
    op.execute("""CREATE FUNCTION public.smm_planner_run_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.profile='content_planner' AND NEW.state IN ('running','needs_review') AND NOT EXISTS (
            SELECT 1 FROM public.ai_inputs i JOIN public.content_records p
              ON p.workspace_id=i.workspace_id AND p.id=i.plan_id
            JOIN public.content_records c ON c.workspace_id=p.workspace_id AND c.id=(p.body->>'campaign_id')::uuid
            WHERE i.workspace_id=NEW.workspace_id AND i.run_id=NEW.id AND i.actor_id=NEW.actor_id
              AND i.planner_context IS NOT NULL AND p.kind='content_plan' AND p.brand_id=NEW.brand_id
              AND p.expires_at>now() AND c.expires_at>now()
              AND NOT EXISTS (SELECT 1 FROM public.content_records n WHERE n.workspace_id=p.workspace_id
                AND ((n.family_id=p.family_id AND n.number>p.number) OR (n.family_id=c.family_id AND n.number>c.number))))
          THEN RAISE EXCEPTION 'planner_current_input_required'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_planner_run_guard() FROM PUBLIC")
    op.execute("CREATE TRIGGER planner_run_guard BEFORE UPDATE ON ai_runs FOR EACH ROW EXECUTE FUNCTION public.smm_planner_run_guard()")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM ai_inputs WHERE planner_context IS NOT NULL)
        THEN RAISE EXCEPTION 'planner_history_requires_restore_plan'; END IF; END $$""")
    op.execute("DROP TRIGGER planner_run_guard ON ai_runs")
    op.execute("DROP FUNCTION public.smm_planner_run_guard()")
    op.execute("DROP TRIGGER planner_input_guard ON ai_inputs")
    op.execute("DROP FUNCTION public.smm_planner_input_guard()")
    op.execute("REVOKE EXECUTE ON FUNCTION public.smm_assignable_member(uuid,uuid) FROM smm_worker")
    op.execute("ALTER TABLE ai_inputs DROP CONSTRAINT ai_input_planner_pair, DROP CONSTRAINT fk_ai_input_plan, DROP COLUMN planner_context, DROP COLUMN plan_id")
