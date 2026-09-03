"""Ingestion recovery and cancellation. Static schema; older migrations are immutable."""

from alembic import op

revision = "0009_ingestion_recovery"
down_revision = "0008_ai_queue"
branch_labels = None
depends_on = None

TABLES = (
    ("knowledge_indexes", "knowledge_index_state"),
    ("knowledge_files", "knowledge_file_state"),
)
RETRY = "'scanner_unavailable','scanner_signatures_stale','sandbox_unavailable','parser_timeout','parser_resource_limit','processing_interrupted'"


def upgrade() -> None:
    op.execute("DROP TRIGGER terminal_immutable ON knowledge_indexes")
    op.execute("DROP TRIGGER file_transition ON knowledge_files")
    for table, constraint in TABLES:
        op.execute(f"""ALTER TABLE {table}
            ADD COLUMN version integer NOT NULL DEFAULT 1,
            ADD COLUMN started_at timestamptz, ADD COLUMN finished_at timestamptz,
            DROP CONSTRAINT {constraint},
            ADD CONSTRAINT {constraint} CHECK (state IN ('queued','processing','ready','failed','cancelled'))""")
        op.execute(f"GRANT UPDATE (version,started_at,finished_at) ON {table} TO smm_worker")
        op.execute(
            f"GRANT UPDATE (state,error_code,version,finished_at,lease_until) ON {table} TO smm_app"
        )
        permitted = "'cancelled','queued'" if table == "knowledge_files" else "'cancelled'"
        op.execute(f"""CREATE POLICY ingestion_update ON {table} AS RESTRICTIVE FOR UPDATE
            USING (public.smm_file_access(workspace_id,actor_id))
            WITH CHECK (public.smm_file_access(workspace_id,actor_id) AND
                (pg_has_role(current_user,'smm_worker','member') OR state IN ({permitted})))""")
    op.execute("""CREATE TABLE knowledge_job_receipts (
        actor_id uuid NOT NULL REFERENCES users(id),
        index_id uuid, file_id uuid, key_hash varchar(64) NOT NULL,
        request_hash varchar(64) NOT NULL, result json NOT NULL,
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,actor_id,key_hash),
        FOREIGN KEY(workspace_id,index_id) REFERENCES knowledge_indexes(workspace_id,id),
        FOREIGN KEY(workspace_id,file_id) REFERENCES knowledge_files(workspace_id,id),
        CONSTRAINT knowledge_job_target CHECK ((index_id IS NULL) <> (file_id IS NULL))
    )""")
    op.execute(
        "CREATE INDEX ix_knowledge_job_receipts_workspace_id ON knowledge_job_receipts(workspace_id)"
    )
    op.execute("ALTER TABLE knowledge_job_receipts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_job_receipts FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY job_receipt_private ON knowledge_job_receipts USING (
        workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND public.smm_file_access(workspace_id,actor_id)
        AND (EXISTS (SELECT 1 FROM public.knowledge_indexes i WHERE i.workspace_id=knowledge_job_receipts.workspace_id
                AND i.id=knowledge_job_receipts.index_id)
          OR EXISTS (SELECT 1 FROM public.knowledge_files f WHERE f.workspace_id=knowledge_job_receipts.workspace_id
                AND f.id=knowledge_job_receipts.file_id)))""")
    op.execute("GRANT SELECT,INSERT ON knowledge_job_receipts TO smm_app")
    op.execute("""CREATE TRIGGER job_receipt_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON knowledge_job_receipts FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute(f"""CREATE FUNCTION public.smm_ingestion_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT ((OLD.state='queued' AND NEW.state IN ('processing','failed','cancelled'))
          OR (OLD.state='processing' AND NEW.state IN ('ready','failed','cancelled'))
          OR (TG_TABLE_NAME='knowledge_files' AND OLD.state='failed' AND NEW.state='queued'
              AND OLD.attempts<3 AND OLD.error_code IN ({RETRY})
              AND OLD.created_at>now()-interval '24 hours')) THEN
            RAISE EXCEPTION 'invalid_ingestion_transition';
        END IF;
        IF NEW.version <> OLD.version+1 THEN RAISE EXCEPTION 'ingestion_version_required'; END IF;
        IF (to_jsonb(NEW)-ARRAY['state','error_code','attempts','lease_id','lease_until','version','started_at','finished_at'])
          IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['state','error_code','attempts','lease_id','lease_until','version','started_at','finished_at']) THEN
            RAISE EXCEPTION 'ingestion_source_immutable';
        END IF;
        IF NEW.state='processing' THEN
            IF NEW.attempts<>OLD.attempts+1 OR NEW.attempts>3 OR NEW.lease_id IS NULL
              OR NEW.lease_id IS NOT DISTINCT FROM OLD.lease_id OR NEW.lease_until IS NULL
              OR NEW.started_at IS NULL OR NEW.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'ingestion_reservation_required';
            END IF;
        ELSE
            IF NEW.attempts<>OLD.attempts OR NEW.lease_id IS DISTINCT FROM OLD.lease_id
              OR NEW.lease_until IS NOT NULL OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION 'ingestion_attempt_immutable';
            END IF;
            IF (NEW.state='queued' AND NEW.finished_at IS NOT NULL)
              OR (NEW.state<>'queued' AND NEW.finished_at IS NULL) THEN
                RAISE EXCEPTION 'ingestion_finished_at_required';
            END IF;
        END IF;
        IF NEW.state='ready' THEN
            IF OLD.lease_until IS NULL OR OLD.lease_until<=now() THEN
                RAISE EXCEPTION 'ingestion_lease_expired';
            END IF;
            IF TG_TABLE_NAME='knowledge_files' THEN
                IF NOT EXISTS (SELECT 1 FROM public.knowledge_extractions e
                    WHERE e.workspace_id=NEW.workspace_id AND e.file_id=NEW.id) THEN
                    RAISE EXCEPTION 'extraction_required';
                END IF;
            ELSE
                IF NOT EXISTS (SELECT 1 FROM public.knowledge_chunks c
                    WHERE c.workspace_id=NEW.workspace_id AND c.index_id=NEW.id) THEN
                    RAISE EXCEPTION 'chunks_required';
                END IF;
            END IF;
        END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ingestion_transition() FROM PUBLIC")
    for table, _ in TABLES:
        op.execute(f"""CREATE TRIGGER ingestion_transition BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION public.smm_ingestion_transition()""")
    op.execute("""CREATE TRIGGER index_no_delete BEFORE DELETE OR TRUNCATE ON knowledge_indexes
        FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    for kind in ("index", "file"):
        table = "knowledge_indexes" if kind == "index" else "knowledge_files"
        name = "smm_knowledge_pending" if kind == "index" else "smm_files_pending"
        id_name = "index_id" if kind == "index" else "file_id"
        limit = 10 if kind == "index" else 5
        op.execute(f"""CREATE OR REPLACE FUNCTION public.{name}()
            RETURNS TABLE(workspace_id uuid,{id_name} uuid,actor_id uuid)
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT x.workspace_id,x.id,x.actor_id FROM public.{table} x
            WHERE x.state='queued' AND x.created_at>now()-interval '24 hours'
              AND {authorized(kind)} {usable(kind)}
            ORDER BY x.created_at,x.id LIMIT {limit} $$""")
    loops = "\n".join(recovery_loop(kind) for kind in ("index", "file"))
    op.execute(f"""CREATE FUNCTION public.smm_ingestion_reconcile(kind text) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE r record; processed integer := 0;
        BEGIN
        IF kind NOT IN ('index','file') OR kind IS NULL THEN
            RAISE EXCEPTION 'invalid_ingestion_kind';
        END IF;
        {loops}
        RETURN processed; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ingestion_reconcile(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_ingestion_reconcile(text) TO smm_worker")
    op.execute("""CREATE TABLE knowledge_job_events (
        actor_id uuid REFERENCES users(id), index_id uuid, file_id uuid,
        version integer NOT NULL, state varchar(24) NOT NULL, attempts integer NOT NULL,
        error_code varchar(80), workspace_id uuid NOT NULL REFERENCES workspaces(id),
        id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,index_id,version), UNIQUE(workspace_id,file_id,version),
        FOREIGN KEY(workspace_id,index_id) REFERENCES knowledge_indexes(workspace_id,id),
        FOREIGN KEY(workspace_id,file_id) REFERENCES knowledge_files(workspace_id,id),
        CONSTRAINT knowledge_event_target CHECK ((index_id IS NULL) <> (file_id IS NULL))
    )""")
    op.execute(
        "CREATE INDEX ix_knowledge_job_events_workspace_id ON knowledge_job_events(workspace_id)"
    )
    op.execute("ALTER TABLE knowledge_job_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_job_events FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY job_event_private ON knowledge_job_events FOR SELECT USING (
        workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid AND (
        EXISTS (SELECT 1 FROM public.knowledge_indexes i WHERE i.workspace_id=knowledge_job_events.workspace_id
            AND i.id=knowledge_job_events.index_id AND public.smm_file_access(i.workspace_id,i.actor_id))
        OR EXISTS (SELECT 1 FROM public.knowledge_files f WHERE f.workspace_id=knowledge_job_events.workspace_id
            AND f.id=knowledge_job_events.file_id AND public.smm_file_access(f.workspace_id,f.actor_id))))""")
    op.execute("GRANT SELECT ON knowledge_job_events TO smm_app")
    op.execute("""CREATE TRIGGER job_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON knowledge_job_events FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("""CREATE FUNCTION public.smm_ingestion_event() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
        INSERT INTO public.knowledge_job_events
            (id,created_at,workspace_id,actor_id,index_id,file_id,version,state,attempts,error_code)
        VALUES (gen_random_uuid(),now(),NEW.workspace_id,
            nullif(current_setting('smm.user_id',true),'')::uuid,
            CASE WHEN TG_TABLE_NAME='knowledge_indexes' THEN NEW.id ELSE NULL END,
            CASE WHEN TG_TABLE_NAME='knowledge_files' THEN NEW.id ELSE NULL END,
            NEW.version,NEW.state,NEW.attempts,NEW.error_code);
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ingestion_event() FROM PUBLIC")
    for table, _ in TABLES:
        op.execute(f"""CREATE TRIGGER ingestion_event AFTER INSERT OR UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION public.smm_ingestion_event()""")


def authorized(kind: str) -> str:
    visibility = (
        """AND (m.role='owner' OR EXISTS (SELECT 1 FROM public.knowledge_documents d
        WHERE d.workspace_id=x.workspace_id AND d.id=x.document_id AND d.visibility='workspace'))"""
        if kind == "index"
        else ""
    )
    return f"""EXISTS (SELECT 1 FROM public.user_identities a
        JOIN public.users u ON u.id=a.user_id
        JOIN public.memberships m ON m.user_id=u.id AND m.workspace_id=x.workspace_id
        WHERE a.id=x.identity_id AND a.user_id=x.actor_id AND a.active AND u.active AND m.active
        AND m.role IN ('owner','editor','strategist') {visibility})"""


def usable(kind: str) -> str:
    if kind == "file":
        return ""
    return """AND EXISTS (SELECT 1 FROM public.knowledge_documents d
        JOIN public.knowledge_document_versions v ON v.document_id=d.id AND v.workspace_id=d.workspace_id
        WHERE d.workspace_id=x.workspace_id AND d.id=x.document_id AND NOT d.archived
        AND v.id=x.document_version_id AND v.effective_to>now())"""


def recovery_loop(kind: str) -> str:
    table = "knowledge_indexes" if kind == "index" else "knowledge_files"
    valid = f"({authorized(kind)} {usable(kind)})"
    return f"""IF kind='{kind}' THEN
        FOR r IN SELECT x.id, x.workspace_id, x.actor_id,
            CASE WHEN NOT {authorized(kind)} THEN 'authorization_changed'
                 WHEN NOT {valid} THEN 'document_unavailable'
                 WHEN x.created_at<=now()-interval '24 hours' THEN 'queue_expired'
                 ELSE 'processing_interrupted' END AS reason
          FROM public.{table} x
          WHERE x.state IN ('queued','processing') AND (
            NOT {valid} OR x.created_at<=now()-interval '24 hours'
            OR (x.state='processing' AND coalesce(x.lease_until,x.created_at+interval '2 minutes')<=now()))
          ORDER BY x.created_at,x.id LIMIT 10 FOR UPDATE OF x SKIP LOCKED LOOP
            UPDATE public.{table} SET state='failed', error_code=r.reason,
                version=version+1, finished_at=now(), lease_until=NULL WHERE id=r.id;
            INSERT INTO public.audit_events
                (id,created_at,workspace_id,actor_id,request_id,action,target_id,outcome,details)
                VALUES (gen_random_uuid(),now(),r.workspace_id,r.actor_id,gen_random_uuid(),
                    'knowledge.{kind}_reconciled',r.id,'failed','{{}}'::json);
            processed := processed+1;
        END LOOP;
        END IF;"""


def downgrade() -> None:
    # Destructive rollback only on disposable data or with explicit restore-backed authorization.
    op.execute("DROP FUNCTION public.smm_ingestion_reconcile(text)")
    for table, _ in TABLES:
        op.execute(f"DROP TRIGGER ingestion_event ON {table}")
    op.execute("DROP FUNCTION public.smm_ingestion_event()")
    op.execute("DROP TABLE knowledge_job_events")
    op.execute("DROP TABLE knowledge_job_receipts")
    op.execute("DROP TRIGGER index_no_delete ON knowledge_indexes")
    for table, constraint in TABLES:
        op.execute(f"DROP TRIGGER ingestion_transition ON {table}")
        op.execute(f"DROP POLICY ingestion_update ON {table}")
        op.execute(f"REVOKE UPDATE (version,started_at,finished_at) ON {table} FROM smm_worker")
        op.execute(
            f"REVOKE UPDATE (state,error_code,version,finished_at,lease_until) ON {table} FROM smm_app"
        )
        op.execute(f"UPDATE {table} SET state='failed' WHERE state='cancelled'")
        op.execute(f"""ALTER TABLE {table} DROP COLUMN version, DROP COLUMN started_at,
            DROP COLUMN finished_at, DROP CONSTRAINT {constraint},
            ADD CONSTRAINT {constraint} CHECK (state IN ('queued','processing','ready','failed'))""")
    op.execute("DROP FUNCTION public.smm_ingestion_transition()")
    op.execute("GRANT UPDATE (state,error_code) ON knowledge_files TO smm_app")
    op.execute("""CREATE TRIGGER terminal_immutable BEFORE UPDATE ON knowledge_indexes
        FOR EACH ROW EXECUTE FUNCTION public.smm_knowledge_terminal()""")
    op.execute("""CREATE TRIGGER file_transition BEFORE UPDATE ON knowledge_files
        FOR EACH ROW EXECUTE FUNCTION public.smm_file_transition()""")
    # Restore the published discovery contract without importing mutable ORM/application code.
    for kind in ("index", "file"):
        table = "knowledge_indexes" if kind == "index" else "knowledge_files"
        name = "smm_knowledge_pending" if kind == "index" else "smm_files_pending"
        id_name = "index_id" if kind == "index" else "file_id"
        limit = 10 if kind == "index" else 5
        op.execute(f"""CREATE OR REPLACE FUNCTION public.{name}()
            RETURNS TABLE(workspace_id uuid,{id_name} uuid,actor_id uuid)
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
            SELECT x.workspace_id,x.id,x.actor_id FROM public.{table} x
            WHERE x.state IN ('queued','processing') AND (x.lease_until IS NULL OR x.lease_until<now())
              AND {authorized(kind)}
            ORDER BY x.created_at,x.id LIMIT {limit} $$""")
