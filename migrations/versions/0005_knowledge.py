"""Text knowledge foundation. Static schema snapshot; published migrations are immutable."""

from alembic import op

revision = "0005_knowledge"
down_revision = "0004_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    op.execute("""CREATE FUNCTION public.smm_knowledge_owner(w uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
          WHERE m.workspace_id=w AND m.user_id=nullif(current_setting('smm.user_id', true), '')::uuid
          AND m.active AND u.active AND m.role='owner') $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_knowledge_owner(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_knowledge_owner(uuid) TO smm_app, smm_worker")
    tenant = "workspace_id = nullif(current_setting('smm.workspace_id', true), '')::uuid AND public.smm_member(workspace_id)"
    actor = "actor_id = nullif(current_setting('smm.user_id', true), '')::uuid"
    for table in TABLES:
        predicate = tenant
        if table == "knowledge_documents":
            predicate += " AND (visibility='workspace' OR public.smm_knowledge_owner(workspace_id))"
        elif table in {"knowledge_indexes", "knowledge_document_versions", "knowledge_chunks"}:
            predicate += f" AND EXISTS (SELECT 1 FROM public.knowledge_documents d WHERE d.id={table}.document_id AND d.workspace_id={table}.workspace_id)"
        elif table == "knowledge_activations":
            predicate += " AND EXISTS (SELECT 1 FROM public.knowledge_indexes i WHERE i.id=knowledge_activations.index_id AND i.workspace_id=knowledge_activations.workspace_id)"
        elif table in {"knowledge_notes", "knowledge_note_reviews"}:
            predicate += " AND public.smm_knowledge_owner(workspace_id)"
        else:
            predicate += " AND " + actor
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY knowledge_boundary ON {table} USING ({predicate}) WITH CHECK ({predicate})")
        op.execute(f"GRANT SELECT ON {table} TO smm_app")
        if table != "knowledge_chunks":
            op.execute(f"GRANT INSERT ON {table} TO smm_app")
        if table not in {"knowledge_documents", "knowledge_indexes", "ai_runs"}:
            op.execute(f"CREATE TRIGGER knowledge_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()")
    op.execute("GRANT UPDATE (version, archived, active_index_id) ON knowledge_documents TO smm_app")
    op.execute("GRANT UPDATE (state, error_code, model, retrieval_run_id, usage) ON ai_runs TO smm_app")
    op.execute("GRANT SELECT ON knowledge_documents, knowledge_document_versions, knowledge_indexes, knowledge_chunks TO smm_worker")
    op.execute("GRANT INSERT ON knowledge_chunks TO smm_worker")
    op.execute("GRANT UPDATE (state, attempts, lease_id, lease_until, error_code) ON knowledge_indexes TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_knowledge_terminal() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN
        IF (TG_TABLE_NAME='knowledge_indexes' AND OLD.state IN ('ready','failed'))
           OR (TG_TABLE_NAME='ai_runs' AND OLD.state <> 'running') THEN
            RAISE EXCEPTION 'terminal_record_immutable';
        END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_knowledge_terminal() FROM PUBLIC")
    for table in ("knowledge_indexes", "ai_runs"):
        op.execute(f"CREATE TRIGGER terminal_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION public.smm_knowledge_terminal()")
    # A worker can discover only bounded identifiers, not document text or arbitrary tenants.
    op.execute("""CREATE FUNCTION public.smm_knowledge_pending()
        RETURNS TABLE(workspace_id uuid, index_id uuid, actor_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT i.workspace_id, i.id, i.actor_id FROM public.knowledge_indexes i
        JOIN public.memberships m ON m.workspace_id=i.workspace_id AND m.user_id=i.actor_id
        JOIN public.users u ON u.id=i.actor_id
        JOIN public.user_identities a ON a.id=i.identity_id AND a.user_id=i.actor_id
        JOIN public.knowledge_documents d ON d.id=i.document_id AND d.workspace_id=i.workspace_id
        WHERE i.state IN ('queued','processing') AND (i.lease_until IS NULL OR i.lease_until < now())
          AND m.active AND u.active AND a.active AND m.role IN ('owner','editor','strategist')
          AND (d.visibility='workspace' OR m.role='owner')
        ORDER BY i.created_at, i.id LIMIT 10 $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_knowledge_pending() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_knowledge_pending() TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_ai_recent_count(w uuid) RETURNS bigint
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT count(*) FROM public.ai_runs r WHERE r.workspace_id=w
        AND public.smm_knowledge_owner(w) AND r.created_at > now() - interval '1 day' $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_recent_count(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_ai_recent_count(uuid) TO smm_app")


def downgrade() -> None:
    # Only disposable test databases or an explicitly approved restore-backed rollback.
    op.execute("DROP FUNCTION public.smm_knowledge_pending()")
    op.execute("DROP FUNCTION public.smm_ai_recent_count(uuid)")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT fk_knowledge_active_index")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION public.smm_knowledge_terminal()")
    op.execute("DROP FUNCTION public.smm_knowledge_owner(uuid)")


TABLES = (
    "knowledge_receipts", "knowledge_documents", "knowledge_notes", "retrieval_runs", "ai_runs",
    "knowledge_document_versions", "knowledge_note_reviews", "ai_artifacts", "knowledge_indexes",
    "knowledge_activations", "knowledge_chunks",
)

DDL = (
    """
CREATE TABLE knowledge_receipts (
	actor_id UUID NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	result JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	UNIQUE (workspace_id, actor_id, key_hash),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_documents (
	brand_id UUID NOT NULL,
	title VARCHAR(200) NOT NULL,
	document_type VARCHAR(32) NOT NULL,
	visibility VARCHAR(24) NOT NULL,
	actor_id UUID NOT NULL,
	version INTEGER NOT NULL,
	archived BOOLEAN NOT NULL,
	active_index_id UUID,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	CONSTRAINT knowledge_visibility CHECK (visibility IN ('workspace','owner')),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_notes (
	actor_id UUID NOT NULL,
	brand_id UUID NOT NULL,
	kind VARCHAR(24) NOT NULL,
	text TEXT NOT NULL,
	purpose TEXT NOT NULL,
	safe_alternative TEXT NOT NULL,
	evidence_ids JSON NOT NULL,
	effective_to TIMESTAMP WITH TIME ZONE NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE retrieval_runs (
	actor_id UUID NOT NULL,
	brand_id UUID NOT NULL,
	query_hash VARCHAR(64) NOT NULL,
	algorithm VARCHAR(80) NOT NULL,
	chunk_ids JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE ai_runs (
	actor_id UUID NOT NULL,
	brand_id UUID NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	profile VARCHAR(32) NOT NULL,
	profile_version VARCHAR(80) NOT NULL,
	profile_snapshot JSON NOT NULL,
	state VARCHAR(24) NOT NULL,
	error_code VARCHAR(80),
	provider VARCHAR(32) NOT NULL,
	model VARCHAR(120) NOT NULL,
	retrieval_run_id UUID,
	usage JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	FOREIGN KEY(workspace_id, retrieval_run_id) REFERENCES retrieval_runs (workspace_id, id),
	UNIQUE (workspace_id, actor_id, key_hash),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_document_versions (
	document_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	original TEXT NOT NULL,
	format VARCHAR(24) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	source_uri VARCHAR(1000) NOT NULL,
	source_date TIMESTAMP WITH TIME ZONE NOT NULL,
	effective_from TIMESTAMP WITH TIME ZONE NOT NULL,
	effective_to TIMESTAMP WITH TIME ZONE NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, document_id) REFERENCES knowledge_documents (workspace_id, id),
	UNIQUE (workspace_id, document_id, id),
	UNIQUE (workspace_id, document_id, fingerprint),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_note_reviews (
	note_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	decision VARCHAR(32) NOT NULL,
	reason TEXT NOT NULL,
	evidence_ids JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, note_id) REFERENCES knowledge_notes (workspace_id, id),
	UNIQUE (workspace_id, note_id),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE ai_artifacts (
	run_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	body JSON NOT NULL,
	citation_ids JSON NOT NULL,
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
CREATE TABLE knowledge_indexes (
	document_id UUID NOT NULL,
	document_version_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	identity_id UUID NOT NULL,
	state VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	lease_id UUID,
	lease_until TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(80),
	parser_version VARCHAR(80) NOT NULL,
	chunking_version VARCHAR(80) NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, document_id) REFERENCES knowledge_documents (workspace_id, id),
	UNIQUE (workspace_id, document_id, id),
	FOREIGN KEY(workspace_id, document_id, document_version_id) REFERENCES knowledge_document_versions (workspace_id, document_id, id),
	CONSTRAINT knowledge_index_state CHECK (state IN ('queued','processing','ready','failed')),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(identity_id) REFERENCES user_identities (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_activations (
	index_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	query_hashes JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, index_id) REFERENCES knowledge_indexes (workspace_id, id),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE knowledge_chunks (
	document_id UUID NOT NULL,
	index_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	section VARCHAR(200) NOT NULL,
	body TEXT NOT NULL,
	search_text TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian', search_text) || to_tsvector('simple', search_text)) STORED NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, document_id) REFERENCES knowledge_documents (workspace_id, id),
	FOREIGN KEY(workspace_id, document_id, index_id) REFERENCES knowledge_indexes (workspace_id, document_id, id),
	UNIQUE (workspace_id, index_id, ordinal),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE INDEX ix_knowledge_receipts_workspace_id ON knowledge_receipts (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_documents_workspace_id ON knowledge_documents (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_notes_workspace_id ON knowledge_notes (workspace_id)
    """,
    """
CREATE INDEX ix_retrieval_runs_workspace_id ON retrieval_runs (workspace_id)
    """,
    """
CREATE INDEX ix_ai_runs_workspace_id ON ai_runs (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_document_versions_workspace_id ON knowledge_document_versions (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_note_reviews_workspace_id ON knowledge_note_reviews (workspace_id)
    """,
    """
CREATE INDEX ix_ai_artifacts_workspace_id ON ai_artifacts (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_indexes_workspace_id ON knowledge_indexes (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_activations_workspace_id ON knowledge_activations (workspace_id)
    """,
    """
CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING gin (search_vector)
    """,
    """
CREATE INDEX ix_knowledge_chunks_workspace_id ON knowledge_chunks (workspace_id)
    """,
    """
ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_active_index FOREIGN KEY(workspace_id, id, active_index_id) REFERENCES knowledge_indexes (workspace_id, document_id, id)
    """,
)
