"""Durable AI dispatch; static snapshot. Published earlier migrations remain immutable."""

from alembic import op

revision = "0008_ai_queue"
down_revision = "0007_knowledge_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    op.execute("""ALTER TABLE ai_runs
        ADD COLUMN identity_id uuid REFERENCES user_identities(id),
        ADD COLUMN version integer NOT NULL DEFAULT 1,
        ADD COLUMN lease_id uuid,
        ADD COLUMN lease_until timestamptz,
        ADD COLUMN started_at timestamptz,
        ADD COLUMN finished_at timestamptz""")
    boundary = """workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND public.smm_knowledge_owner(workspace_id)"""
    for table in ("ai_inputs", "ai_cancel_receipts"):
        predicate = (
            boundary
            + f""" AND EXISTS (SELECT 1 FROM public.ai_runs r
            WHERE r.workspace_id={table}.workspace_id AND r.id={table}.run_id
            AND r.actor_id={table}.actor_id)"""
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY ai_private ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO smm_app")
        op.execute(f"""CREATE TRIGGER ai_input_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("GRANT SELECT ON ai_runs,ai_inputs TO smm_worker")
    op.execute("GRANT SELECT,INSERT ON ai_artifacts TO smm_worker")
    op.execute("REVOKE INSERT ON ai_artifacts FROM smm_app")
    op.execute("""CREATE POLICY ai_result_parent ON ai_artifacts AS RESTRICTIVE FOR INSERT
        WITH CHECK (EXISTS (SELECT 1 FROM public.ai_runs r WHERE r.id=ai_artifacts.run_id
            AND r.workspace_id=ai_artifacts.workspace_id AND r.actor_id=ai_artifacts.actor_id
            AND r.state IN ('running','cancel_requested')))""")
    op.execute("""REVOKE UPDATE (state,error_code,model,retrieval_run_id,usage)
        ON ai_runs FROM smm_app""")
    op.execute("GRANT UPDATE (state,version,finished_at) ON ai_runs TO smm_app")
    op.execute("""GRANT UPDATE (state,version,error_code,model,usage,started_at,finished_at,
        lease_id,lease_until) ON ai_runs TO smm_worker""")
    op.execute("DROP TRIGGER terminal_immutable ON ai_runs")
    op.execute("""CREATE FUNCTION public.smm_ai_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT ((OLD.state='queued' AND NEW.state IN ('running','blocked','failed','cancelled'))
           OR (OLD.state='running' AND NEW.state IN ('needs_review','failed','unknown','cancel_requested'))
           OR (OLD.state='cancel_requested' AND NEW.state IN ('cancelled','unknown'))) THEN
            RAISE EXCEPTION 'invalid_ai_transition';
        END IF;
        IF NEW.version <> OLD.version+1 THEN RAISE EXCEPTION 'ai_version_required'; END IF;
        IF (to_jsonb(NEW)-ARRAY['state','version','error_code','model','usage','started_at',
                              'finished_at','lease_id','lease_until'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['state','version','error_code','model','usage','started_at',
                              'finished_at','lease_id','lease_until']) THEN
            RAISE EXCEPTION 'ai_input_immutable';
        END IF;
        IF NEW.state='running' AND (NEW.lease_id IS NULL OR NEW.lease_until IS NULL
            OR NEW.started_at IS NULL OR NEW.identity_id IS NULL
            OR (NEW.usage->>'attempts') IS DISTINCT FROM '1') THEN
            RAISE EXCEPTION 'dispatch_reservation_required';
        END IF;
        IF OLD.state IN ('running','cancel_requested') AND NEW.lease_id IS DISTINCT FROM OLD.lease_id THEN
            RAISE EXCEPTION 'ai_lease_immutable';
        END IF;
        IF NEW.state='needs_review' AND NOT EXISTS (SELECT 1 FROM public.ai_artifacts a
            WHERE a.run_id=NEW.id AND a.workspace_id=NEW.workspace_id AND a.actor_id=NEW.actor_id) THEN
            RAISE EXCEPTION 'ai_artifact_required';
        END IF;
        IF NEW.state NOT IN ('running','cancel_requested') AND NEW.finished_at IS NULL THEN
            RAISE EXCEPTION 'ai_finished_at_required';
        END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_transition() FROM PUBLIC")
    op.execute("""CREATE TRIGGER terminal_immutable BEFORE UPDATE ON ai_runs
        FOR EACH ROW EXECUTE FUNCTION public.smm_ai_transition()""")
    op.execute("""CREATE TRIGGER ai_no_delete BEFORE DELETE OR TRUNCATE ON ai_runs
        FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("""CREATE FUNCTION public.smm_ai_pending()
        RETURNS TABLE(workspace_id uuid,run_id uuid,actor_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT r.workspace_id,r.id,r.actor_id FROM public.ai_runs r
        JOIN public.user_identities a ON a.id=r.identity_id AND a.user_id=r.actor_id
        JOIN public.users u ON u.id=r.actor_id
        JOIN public.memberships m ON m.user_id=r.actor_id AND m.workspace_id=r.workspace_id
        WHERE r.state='queued' AND r.created_at>now()-interval '24 hours'
        AND a.active AND u.active AND m.active AND m.role='owner'
        ORDER BY r.created_at,r.id LIMIT 5 $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_pending() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_ai_pending() TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_ai_reconcile() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE r public.ai_runs%ROWTYPE; processed integer := 0; next_state text; code text;
        BEGIN
        FOR r IN SELECT * FROM public.ai_runs x WHERE
          (x.state IN ('running','cancel_requested')
           AND coalesce(x.lease_until,x.created_at+interval '2 minutes')<=now())
          OR (x.state='queued' AND (x.created_at<=now()-interval '24 hours' OR NOT EXISTS (
            SELECT 1 FROM public.user_identities a JOIN public.users u ON u.id=a.user_id
            JOIN public.memberships m ON m.user_id=u.id AND m.workspace_id=x.workspace_id
            WHERE a.id=x.identity_id AND a.user_id=x.actor_id
            AND a.active AND u.active AND m.active AND m.role='owner')))
          ORDER BY x.created_at,x.id LIMIT 10 FOR UPDATE SKIP LOCKED LOOP
            IF r.state='queued' THEN
                next_state := 'blocked';
                code := CASE WHEN r.created_at<=now()-interval '24 hours'
                    THEN 'queue_expired' ELSE 'authorization_changed' END;
            ELSE
                next_state := 'unknown';
                code := 'interrupted_run_not_replayed';
            END IF;
            UPDATE public.ai_runs SET state=next_state, error_code=code,
                version=version+1, finished_at=now(), lease_until=NULL
                WHERE id=r.id;
            INSERT INTO public.audit_events
                (id,created_at,workspace_id,actor_id,request_id,action,target_id,outcome,details)
                VALUES (gen_random_uuid(),now(),r.workspace_id,r.actor_id,gen_random_uuid(),
                        'ai.run_reconciled',r.id,next_state,'{}'::json);
            processed := processed+1;
        END LOOP;
        RETURN processed; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_reconcile() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_ai_reconcile() TO smm_worker")


def downgrade() -> None:
    # Destructive data rollback: disposable tests or explicitly authorized restore only.
    op.execute("DROP FUNCTION public.smm_ai_pending()")
    op.execute("DROP FUNCTION public.smm_ai_reconcile()")
    op.execute("DROP TRIGGER terminal_immutable ON ai_runs")
    op.execute("DROP TRIGGER ai_no_delete ON ai_runs")
    op.execute("DROP FUNCTION public.smm_ai_transition()")
    op.execute("""CREATE TRIGGER terminal_immutable BEFORE UPDATE ON ai_runs
        FOR EACH ROW EXECUTE FUNCTION public.smm_knowledge_terminal()""")
    op.execute("REVOKE SELECT ON ai_runs FROM smm_worker")
    op.execute("REVOKE SELECT,INSERT ON ai_artifacts FROM smm_worker")
    op.execute("GRANT INSERT ON ai_artifacts TO smm_app")
    op.execute("DROP POLICY ai_result_parent ON ai_artifacts")
    op.execute("""REVOKE UPDATE (state,version,error_code,model,usage,started_at,finished_at,
        lease_id,lease_until) ON ai_runs FROM smm_worker""")
    op.execute("REVOKE UPDATE (state,version,finished_at) ON ai_runs FROM smm_app")
    op.execute("GRANT UPDATE (state,error_code,model,retrieval_run_id,usage) ON ai_runs TO smm_app")
    op.execute("DROP TABLE ai_cancel_receipts")
    op.execute("DROP TABLE ai_inputs")
    for column in (
        "identity_id",
        "version",
        "lease_id",
        "lease_until",
        "started_at",
        "finished_at",
    ):
        op.execute(f"ALTER TABLE ai_runs DROP COLUMN {column}")


DDL = (
    """
CREATE TABLE ai_inputs (
    run_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    question TEXT NOT NULL,
    citations JSON NOT NULL,
    payload JSON NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, run_id) REFERENCES ai_runs (workspace_id, id),
    UNIQUE (workspace_id, run_id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)

""",
    """
CREATE TABLE ai_cancel_receipts (
    run_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    result JSON NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, run_id) REFERENCES ai_runs (workspace_id, id),
    UNIQUE (workspace_id, actor_id, key_hash),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)

""",
    """CREATE INDEX ix_ai_inputs_workspace_id ON ai_inputs (workspace_id)""",
    """CREATE INDEX ix_ai_cancel_receipts_workspace_id ON ai_cancel_receipts (workspace_id)""",
)
