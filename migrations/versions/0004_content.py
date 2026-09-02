"""Immutable SQL snapshot for the phase-six content lifecycle (no live ORM imports)."""

import sqlalchemy as sa
from alembic import op

revision = "0004_content"
down_revision = "0003_operations"
branch_labels = None
depends_on = None

TABLES = [
    "content_receipts",
    "content_records",
    "work_item_dependencies",
    "content_links",
    "posts",
    "work_assignments",
    "post_revisions",
    "post_working_copies",
    "content_comments",
    "content_decisions",
    "content_review_runs",
    "publication_packages",
    "package_cancellations",
]
STATEMENTS = [
    """CREATE TABLE content_receipts (
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
    """CREATE TABLE content_records (
    brand_id UUID NOT NULL,
    source_id UUID,
    product_id UUID,
    family_id UUID NOT NULL,
    number INTEGER NOT NULL,
    kind VARCHAR(32) NOT NULL,
    body JSON NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    actor_id UUID NOT NULL,
    confirmed_by UUID,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
    FOREIGN KEY(workspace_id, source_id) REFERENCES sources (workspace_id, id),
    FOREIGN KEY(workspace_id, product_id) REFERENCES products (workspace_id, id),
    UNIQUE (workspace_id, family_id, number),
    CONSTRAINT content_record_number CHECK (number >= 1),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(confirmed_by) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE work_item_dependencies (
    item_id UUID NOT NULL,
    depends_on UUID NOT NULL,
    active BOOLEAN NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, item_id) REFERENCES work_items (workspace_id, id),
    FOREIGN KEY(workspace_id, depends_on) REFERENCES work_items (workspace_id, id),
    UNIQUE (workspace_id, item_id, depends_on),
    CONSTRAINT work_dependency_self CHECK (item_id <> depends_on),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE content_links (
    record_id UUID NOT NULL,
    target_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, record_id) REFERENCES content_records (workspace_id, id),
    FOREIGN KEY(workspace_id, target_id) REFERENCES content_records (workspace_id, id),
    UNIQUE (workspace_id, record_id, target_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE posts (
    brand_id UUID NOT NULL,
    brief_id UUID NOT NULL,
    idea_id UUID,
    title VARCHAR(200) NOT NULL,
    version INTEGER NOT NULL,
    revision_count INTEGER NOT NULL,
    state VARCHAR(24) NOT NULL,
    current_revision_id UUID,
    active_approval_id UUID,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
    FOREIGN KEY(workspace_id, brief_id) REFERENCES content_records (workspace_id, id),
    FOREIGN KEY(workspace_id, idea_id) REFERENCES content_records (workspace_id, id),
    CONSTRAINT post_state
        CHECK (state IN ('draft','in_review','rejected','approved','package_ready')),
    CONSTRAINT post_version CHECK (version >= 1 AND revision_count >= 0),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE work_assignments (
    item_id UUID NOT NULL,
    campaign_id UUID,
    assignee_id UUID NOT NULL,
    due_at TIMESTAMP WITH TIME ZONE NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, item_id) REFERENCES work_items (workspace_id, id),
    FOREIGN KEY(workspace_id, campaign_id) REFERENCES content_records (workspace_id, id),
    UNIQUE (workspace_id, item_id),
    FOREIGN KEY(workspace_id, assignee_id) REFERENCES memberships (workspace_id, user_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE post_revisions (
    post_id UUID NOT NULL,
    number INTEGER NOT NULL,
    actor_id UUID NOT NULL,
    body JSON NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    UNIQUE (workspace_id, post_id, number),
    UNIQUE (workspace_id, post_id, id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE post_working_copies (
    post_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    version INTEGER NOT NULL,
    base_version INTEGER NOT NULL,
    body JSON NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    UNIQUE (workspace_id, post_id, actor_id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE content_comments (
    post_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    text TEXT NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id, revision_id)
        REFERENCES post_revisions (workspace_id, post_id, id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE content_decisions (
    post_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    decision VARCHAR(16) NOT NULL,
    reason VARCHAR(200) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    preflight JSON NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id, revision_id)
        REFERENCES post_revisions (workspace_id, post_id, id),
    UNIQUE (workspace_id, post_id, id),
    CONSTRAINT content_decision CHECK (decision IN ('approve','reject')),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE content_review_runs (
    post_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    result JSON NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id, revision_id)
        REFERENCES post_revisions (workspace_id, post_id, id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE publication_packages (
    post_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    approval_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
    timezone VARCHAR(80) NOT NULL,
    manifest JSON NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id) REFERENCES posts (workspace_id, id),
    FOREIGN KEY(workspace_id, post_id, revision_id)
        REFERENCES post_revisions (workspace_id, post_id, id),
    FOREIGN KEY(workspace_id, post_id, approval_id)
        REFERENCES content_decisions (workspace_id, post_id, id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """CREATE TABLE package_cancellations (
    package_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY(workspace_id, package_id) REFERENCES publication_packages (workspace_id, id),
    UNIQUE (workspace_id, package_id),
    FOREIGN KEY(actor_id) REFERENCES users (id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    "CREATE INDEX ix_content_receipts_workspace_id ON content_receipts (workspace_id)",
    "CREATE INDEX ix_content_records_workspace_id ON content_records (workspace_id)",
    "CREATE INDEX ix_work_item_dependencies_workspace_id ON work_item_dependencies (workspace_id)",
    "CREATE INDEX ix_content_links_workspace_id ON content_links (workspace_id)",
    "CREATE INDEX ix_posts_workspace_id ON posts (workspace_id)",
    "CREATE INDEX ix_work_assignments_workspace_id ON work_assignments (workspace_id)",
    "CREATE INDEX ix_post_revisions_workspace_id ON post_revisions (workspace_id)",
    "CREATE INDEX ix_post_working_copies_workspace_id ON post_working_copies (workspace_id)",
    "CREATE INDEX ix_content_comments_workspace_id ON content_comments (workspace_id)",
    "CREATE INDEX ix_content_decisions_workspace_id ON content_decisions (workspace_id)",
    "CREATE INDEX ix_content_review_runs_workspace_id ON content_review_runs (workspace_id)",
    "CREATE INDEX ix_publication_packages_workspace_id ON publication_packages (workspace_id)",
    "CREATE INDEX ix_package_cancellations_workspace_id ON package_cancellations (workspace_id)",
    """ALTER TABLE posts ADD CONSTRAINT fk_post_current_revision
    FOREIGN KEY(workspace_id, id, current_revision_id)
    REFERENCES post_revisions (workspace_id, post_id, id)""",
    """ALTER TABLE posts ADD CONSTRAINT fk_post_active_approval
    FOREIGN KEY(workspace_id, id, active_approval_id)
    REFERENCES content_decisions (workspace_id, post_id, id)""",
]
MUTABLE = {"posts", "post_working_copies", "work_item_dependencies", "work_assignments"}


def upgrade() -> None:
    op.execute("GRANT INSERT ON brands, products, sources TO smm_app")
    for name in ("brands", "products", "sources", "work_items"):
        op.execute(f"ALTER TABLE {name} ADD UNIQUE (workspace_id, id)")
    for statement in STATEMENTS:
        op.execute(statement)
    op.add_column("post_revisions", sa.Column("media_manifest", sa.JSON(), nullable=False))
    op.execute("""
        CREATE FUNCTION smm_assignable_member(wid uuid, uid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
          SELECT public.smm_member(wid) AND wid =
            nullif(current_setting('smm.workspace_id', true), '')::uuid AND EXISTS (
              SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
              WHERE m.workspace_id=wid AND m.user_id=uid AND m.active AND u.active)
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION smm_assignable_member(uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION smm_assignable_member(uuid,uuid) TO smm_app")
    op.execute("""
        CREATE FUNCTION smm_content_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN RAISE EXCEPTION 'content_history_is_immutable'; END $$
    """)
    for name in TABLES:
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        personal = (
            " AND actor_id = nullif(current_setting('smm.user_id', true), '')::uuid"
            if name == "post_working_copies"
            else ""
        )
        op.execute(
            f"CREATE POLICY tenant ON {name} USING (workspace_id = "
            "nullif(current_setting('smm.workspace_id', true), '')::uuid "
            f"AND smm_member(workspace_id){personal})"
        )
        op.execute(f"GRANT SELECT, INSERT ON {name} TO smm_app")
        if name in MUTABLE:
            op.execute(f"GRANT UPDATE ON {name} TO smm_app")
        else:
            op.execute(
                f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {name} "
                "FOR EACH ROW EXECUTE FUNCTION smm_content_immutable()"
            )
            op.execute(
                f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {name} "
                "FOR EACH STATEMENT EXECUTE FUNCTION smm_content_immutable()"
            )
    op.execute("GRANT DELETE ON post_working_copies TO smm_app")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON brands, products, sources FROM smm_app")
    # Destructive: disposable tests only, or separately approved restore-backed rollback.
    op.drop_constraint("fk_post_current_revision", "posts", type_="foreignkey")
    op.drop_constraint("fk_post_active_approval", "posts", type_="foreignkey")
    for name in reversed(TABLES):
        op.drop_table(name)
    op.execute("DROP FUNCTION smm_content_immutable()")
    op.execute("DROP FUNCTION smm_assignable_member(uuid,uuid)")
    for name in ("brands", "products", "sources", "work_items"):
        op.drop_constraint(f"{name}_workspace_id_id_key", name, type_="unique")
