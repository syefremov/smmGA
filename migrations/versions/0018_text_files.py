"""Allowlisted immutable text-file originals, with fail-closed old-code rollback."""

from alembic import op

revision = "0018_text_files"
down_revision = "0017_plan_adoption"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE knowledge_files ADD CONSTRAINT knowledge_file_format
        CHECK (format IN ('pdf','docx','markdown','csv','html'))""")


def downgrade() -> None:
    # A migration operator must see all workspaces; RLS filtering must not hide saved originals.
    op.execute("SET LOCAL row_security=off")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_files
        WHERE format NOT IN ('pdf','docx')) THEN
        RAISE EXCEPTION 'text_file_history_requires_restore_plan'; END IF; END $$""")
    op.execute("ALTER TABLE knowledge_files DROP CONSTRAINT knowledge_file_format")
