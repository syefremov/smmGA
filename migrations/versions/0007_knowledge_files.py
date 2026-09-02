"""Private file quarantine and extraction. Static snapshot; published migrations immutable."""

from alembic import op

revision = "0007_knowledge_files"
down_revision = "0006_retrieval_eval"
branch_labels = None
depends_on = None
TABLES = ["knowledge_files", "knowledge_extractions", "knowledge_file_retry_receipts"]


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    op.execute("ALTER TABLE knowledge_document_versions ADD COLUMN source_file_id uuid")
    op.execute(
        "ALTER TABLE knowledge_document_versions ADD CONSTRAINT fk_knowledge_source_file FOREIGN KEY (workspace_id,source_file_id) REFERENCES knowledge_files(workspace_id,id)"
    )
    op.execute("""CREATE FUNCTION public.smm_file_access(w uuid, a uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
        WHERE m.workspace_id=w AND u.id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND m.active AND u.active AND (m.role='owner' OR (m.role IN ('editor','strategist') AND u.id=a))) $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_file_access(uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_file_access(uuid,uuid) TO smm_app, smm_worker")
    tenant = "workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid AND public.smm_member(workspace_id)"
    actor = "actor_id=nullif(current_setting('smm.user_id',true),'')::uuid"
    for table in TABLES:
        read = tenant
        if table == "knowledge_files":
            read += " AND public.smm_file_access(workspace_id,actor_id)"
        else:
            read += f" AND EXISTS (SELECT 1 FROM public.knowledge_files f WHERE f.workspace_id={table}.workspace_id AND f.id={table}.file_id)"
            if table == "knowledge_file_retry_receipts":
                read += " AND " + actor
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY file_read ON {table} FOR SELECT USING ({read})")
        insert = read if table == "knowledge_extractions" else read + " AND " + actor
        op.execute(f"CREATE POLICY file_insert ON {table} FOR INSERT WITH CHECK ({insert})")
        op.execute(f"GRANT SELECT ON {table} TO smm_app")
        if table != "knowledge_extractions":
            op.execute(f"GRANT INSERT ON {table} TO smm_app")
        else:
            op.execute(f"GRANT SELECT,INSERT ON {table} TO smm_worker")
        if table != "knowledge_files":
            op.execute(
                f"CREATE TRIGGER file_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()"
            )
    op.execute(
        f"CREATE POLICY file_update ON knowledge_files FOR UPDATE USING ({tenant} AND public.smm_file_access(workspace_id,actor_id))"
    )
    op.execute("GRANT SELECT ON knowledge_files TO smm_worker")
    op.execute("GRANT UPDATE (state,error_code) ON knowledge_files TO smm_app")
    op.execute(
        "GRANT UPDATE (state,error_code,attempts,lease_id,lease_until) ON knowledge_files TO smm_worker"
    )
    op.execute("""CREATE FUNCTION public.smm_file_transition() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF OLD.state='ready' OR
          (OLD.state='failed' AND NOT (NEW.state='queued' AND OLD.attempts<3 AND NEW.attempts=OLD.attempts AND
          OLD.error_code IN ('scanner_unavailable','scanner_signatures_stale','sandbox_unavailable','parser_timeout','parser_resource_limit'))) OR
          (OLD.state='queued' AND NEW.state NOT IN ('processing','failed')) OR
          (OLD.state='processing' AND NEW.state NOT IN ('processing','ready','failed')) THEN
          RAISE EXCEPTION 'invalid_file_transition';
        END IF;
        IF (to_jsonb(NEW)-ARRAY['state','error_code','attempts','lease_id','lease_until'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','error_code','attempts','lease_id','lease_until']) THEN
          RAISE EXCEPTION 'original_metadata_immutable';
        END IF;
        IF NEW.state='ready' AND NOT EXISTS (SELECT 1 FROM public.knowledge_extractions e
          WHERE e.workspace_id=NEW.workspace_id AND e.file_id=NEW.id) THEN
          RAISE EXCEPTION 'extraction_required';
        END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_file_transition() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER file_transition BEFORE UPDATE ON knowledge_files FOR EACH ROW EXECUTE FUNCTION public.smm_file_transition()"
    )
    op.execute(
        "CREATE TRIGGER file_no_delete BEFORE DELETE OR TRUNCATE ON knowledge_files FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()"
    )
    op.execute("""CREATE FUNCTION public.smm_files_pending()
        RETURNS TABLE(workspace_id uuid,file_id uuid,actor_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        SELECT f.workspace_id,f.id,f.actor_id FROM public.knowledge_files f
        JOIN public.memberships m ON m.workspace_id=f.workspace_id AND m.user_id=f.actor_id
        JOIN public.users u ON u.id=f.actor_id
        JOIN public.user_identities a ON a.id=f.identity_id AND a.user_id=f.actor_id
        WHERE f.state IN ('queued','processing') AND (f.lease_until IS NULL OR f.lease_until<now())
        AND m.active AND u.active AND a.active AND m.role IN ('owner','editor','strategist')
        ORDER BY f.created_at,f.id LIMIT 5 $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_files_pending() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_files_pending() TO smm_worker")


def downgrade() -> None:
    # Disposable test DB only, or explicitly authorized restore-backed destructive rollback.
    op.execute("DROP FUNCTION public.smm_files_pending()")
    op.execute("ALTER TABLE knowledge_document_versions DROP CONSTRAINT fk_knowledge_source_file")
    op.execute("ALTER TABLE knowledge_document_versions DROP COLUMN source_file_id")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION public.smm_file_transition()")
    op.execute("DROP FUNCTION public.smm_file_access(uuid,uuid)")


DDL = (
    """
CREATE TABLE knowledge_files (
	brand_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	identity_id UUID NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	filename VARCHAR(160) NOT NULL,
	format VARCHAR(8) NOT NULL,
	byte_size INTEGER NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	state VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	lease_id UUID,
	lease_until TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(80),
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	CONSTRAINT knowledge_file_size CHECK (byte_size > 0 AND byte_size <= 2097152),
	CONSTRAINT knowledge_file_state CHECK (state IN ('queued','processing','ready','failed')),
	UNIQUE (workspace_id, actor_id, key_hash),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(identity_id) REFERENCES user_identities (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_extractions (
	file_id UUID NOT NULL,
	text TEXT NOT NULL,
	text_hash VARCHAR(64) NOT NULL,
	parser_version VARCHAR(100) NOT NULL,
	scan_engine VARCHAR(100) NOT NULL,
	signature_version VARCHAR(32) NOT NULL,
	signatures_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	scanned_at TIMESTAMP WITH TIME ZONE NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, file_id) REFERENCES knowledge_files (workspace_id, id),
	UNIQUE (workspace_id, file_id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_file_retry_receipts (
	actor_id UUID NOT NULL,
	file_id UUID NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	result JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, file_id) REFERENCES knowledge_files (workspace_id, id),
	UNIQUE (workspace_id, actor_id, key_hash),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE INDEX ix_knowledge_files_workspace_id ON knowledge_files (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_extractions_workspace_id ON knowledge_extractions (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_file_retry_receipts_workspace_id ON knowledge_file_retry_receipts (workspace_id)
    """,
)
