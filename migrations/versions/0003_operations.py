"""Shared application work items and bounded tenant reference views.

Revision ID: 0003_operations
Revises: 0002_identity
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_operations"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("workspace_id", "actor_id", "key_hash"),
        sa.CheckConstraint("state IN ('open','in_progress','done','cancelled')", name="work_item_state"),
        sa.CheckConstraint("version >= 1", name="work_item_version"),
    )
    for name in ("brands", "products", "sources"):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
        )
    for name in ("work_items", "brands", "products", "sources"):
        op.create_index(f"ix_{name}_workspace_id", name, ["workspace_id"])
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant ON {name} USING (workspace_id = "
            "nullif(current_setting('smm.workspace_id', true), '')::uuid "
            "AND smm_member(workspace_id))"
        )
        op.execute(f"GRANT SELECT ON {name} TO smm_app")
    op.execute("GRANT INSERT, UPDATE ON work_items TO smm_app")
    # The sole cross-workspace read is the caller's own active memberships.
    # Fixed search path, explicit schemas, no supplied user ID, no arbitrary SQL.
    op.execute("""
        CREATE FUNCTION smm_my_workspaces()
        RETURNS TABLE(id uuid, name varchar, timezone varchar, role varchar)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
          SELECT w.id, w.name, w.timezone, m.role
          FROM public.memberships m JOIN public.workspaces w ON w.id = m.workspace_id
          JOIN public.users u ON u.id = m.user_id
          WHERE m.user_id = nullif(current_setting('smm.user_id', true), '')::uuid
            AND m.active AND u.active ORDER BY w.id LIMIT 101
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION smm_my_workspaces() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION smm_my_workspaces() TO smm_app")


def downgrade() -> None:
    # Only for disposable tests or an explicitly approved data rollback.
    op.execute("DROP FUNCTION smm_my_workspaces()")
    for name in ("sources", "products", "brands", "work_items"):
        op.drop_table(name)
