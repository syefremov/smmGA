"""Identity foundation and enforced tenant boundaries. Frozen schema snapshot."""

from alembic import op

revision = "0002_identity"
down_revision = "0001_phase_two"
branch_labels = None
depends_on = None

DDL = (
    """
CREATE TABLE login_flows (
    state_hash VARCHAR(64) NOT NULL,
    browser_hash VARCHAR(64) NOT NULL,
    verifier VARCHAR(128) NOT NULL,
    nonce VARCHAR(128) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (state_hash)
)
    """,
    """
CREATE TABLE users (
    display_name VARCHAR(120) NOT NULL,
    active BOOLEAN NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
)
    """,
    """
CREATE TABLE workspaces (
    slug VARCHAR(80) NOT NULL,
    name VARCHAR(120) NOT NULL,
    timezone VARCHAR(80) NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (slug)
)
    """,
    """
CREATE TABLE audit_events (
    workspace_id UUID,
    actor_id UUID,
    request_id UUID NOT NULL,
    action VARCHAR(80) NOT NULL,
    target_id UUID,
    outcome VARCHAR(24) NOT NULL,
    details JSON NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
    FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """,
    """
CREATE TABLE memberships (
    workspace_id UUID NOT NULL,
    user_id UUID NOT NULL,
    role VARCHAR(24) NOT NULL,
    active BOOLEAN NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, user_id),
    CONSTRAINT membership_role CHECK (role IN ('owner','administrator','strategist','editor','publisher','analyst','viewer')),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
)
    """,
    """
CREATE TABLE system_jobs (
    workspace_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    kind VARCHAR(80) NOT NULL,
    state VARCHAR(24) NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CONSTRAINT job_state CHECK (state IN ('pending','running','succeeded','failed')),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
    FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """,
    """
CREATE TABLE user_identities (
    user_id UUID NOT NULL,
    issuer VARCHAR(512) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    active BOOLEAN NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (issuer, subject),
    FOREIGN KEY(user_id) REFERENCES users (id)
)
    """,
    """
CREATE TABLE file_metadata (
    workspace_id UUID NOT NULL,
    job_id UUID,
    storage_key VARCHAR(255) NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    size_bytes INTEGER NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, storage_key),
    CONSTRAINT file_size CHECK (size_bytes >= 0),
    FOREIGN KEY(workspace_id, job_id) REFERENCES system_jobs (workspace_id, id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE idempotency_keys (
    workspace_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    operation VARCHAR(80) NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    job_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, actor_id, operation, key_hash),
    FOREIGN KEY(workspace_id, job_id) REFERENCES system_jobs (workspace_id, id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id),
    FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """,
    """
CREATE TABLE outbox_events (
    workspace_id UUID NOT NULL,
    job_id UUID NOT NULL,
    kind VARCHAR(80) NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, job_id, kind),
    FOREIGN KEY(workspace_id, job_id) REFERENCES system_jobs (workspace_id, id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE web_sessions (
    identity_id UUID NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    csrf_hash VARCHAR(64) NOT NULL,
    mfa BOOLEAN NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(identity_id) REFERENCES user_identities (id),
    UNIQUE (token_hash)
)
    """,
    """
CREATE INDEX ix_audit_events_workspace_id ON audit_events (workspace_id)
    """,
)


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'smm_app') THEN
            CREATE ROLE smm_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'smm_worker') THEN
            CREATE ROLE smm_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
        END IF;
    END $$""")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO smm_app, smm_worker")
    op.execute("""CREATE FUNCTION public.smm_member(w uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
          WHERE m.workspace_id=w AND m.user_id=nullif(current_setting('smm.user_id', true), '')::uuid
          AND m.active AND u.active)
        $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_member(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_member(uuid) TO smm_app, smm_worker")
    tenant = "workspace_id = nullif(current_setting('smm.workspace_id', true), '')::uuid"
    for table in ("workspaces", "memberships", "system_jobs", "file_metadata", "idempotency_keys", "outbox_events", "audit_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if table == "workspaces":
            predicate = tenant.replace("workspace_id =", "id =") + " AND public.smm_member(id)"
        elif table == "memberships":
            predicate = tenant + " AND user_id = nullif(current_setting('smm.user_id', true), '')::uuid"
        else:
            predicate = tenant + " AND public.smm_member(workspace_id)"
        if table == "audit_events":
            op.execute(f"CREATE POLICY tenant_read ON {table} FOR SELECT USING ({predicate})")
            op.execute(f"CREATE POLICY audit_append ON {table} FOR INSERT WITH CHECK (true)")
        else:
            op.execute(f"CREATE POLICY tenant_boundary ON {table} USING ({predicate}) WITH CHECK ({predicate})")
    op.execute("GRANT SELECT ON users, user_identities, workspaces, memberships TO smm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON web_sessions, login_flows TO smm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON system_jobs, file_metadata, idempotency_keys, outbox_events TO smm_app")
    op.execute("GRANT SELECT ON users, user_identities, memberships, workspaces TO smm_worker")
    op.execute("GRANT SELECT, UPDATE ON system_jobs, outbox_events TO smm_worker")
    op.execute("GRANT SELECT, INSERT ON audit_events TO smm_app, smm_worker")
    op.execute("""CREATE FUNCTION public.smm_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN
        RAISE EXCEPTION 'audit_is_append_only'; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_audit_immutable() FROM PUBLIC")
    op.execute("""CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")


def downgrade() -> None:
    # Disposable database only. Production rollback is forward-only.
    for table in ("outbox_events", "idempotency_keys", "file_metadata", "system_jobs", "web_sessions",
                  "login_flows", "audit_events", "memberships", "workspaces", "user_identities", "users"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION public.smm_member(uuid)")
    op.execute("DROP FUNCTION public.smm_audit_immutable()")
    # Cluster-wide group roles are deliberately retained; no credentials or bypass granted.
