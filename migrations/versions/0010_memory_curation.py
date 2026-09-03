"""Owner-reviewed proposal provenance. Static, additive migration."""

from alembic import op

revision = "0010_memory_curation"
down_revision = "0009_ingestion_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_indexes ADD CONSTRAINT uq_knowledge_index_version_ref UNIQUE (workspace_id, document_id, document_version_id, id)"
    )
    op.execute(
        "ALTER TABLE knowledge_note_reviews ADD CONSTRAINT uq_knowledge_note_review_ref UNIQUE (workspace_id, note_id, id)"
    )
    op.execute("""
        CREATE TABLE knowledge_memory_documents (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            note_id UUID NOT NULL,
            review_id UUID NOT NULL,
            actor_id UUID NOT NULL REFERENCES users(id),
            document_id UUID NOT NULL,
            document_version_id UUID NOT NULL,
            index_id UUID NOT NULL,
            context_hash VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            evidence JSON NOT NULL,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, note_id),
            UNIQUE (workspace_id, document_id),
            FOREIGN KEY (workspace_id, note_id) REFERENCES knowledge_notes(workspace_id, id),
            FOREIGN KEY (workspace_id, document_id) REFERENCES knowledge_documents(workspace_id, id),
            CONSTRAINT fk_memory_note_review FOREIGN KEY (workspace_id, note_id, review_id)
                REFERENCES knowledge_note_reviews(workspace_id, note_id, id),
            CONSTRAINT fk_memory_document_version FOREIGN KEY (workspace_id, document_id, document_version_id)
                REFERENCES knowledge_document_versions(workspace_id, document_id, id),
            CONSTRAINT fk_memory_document_index FOREIGN KEY (workspace_id, document_id, document_version_id, index_id)
                REFERENCES knowledge_indexes(workspace_id, document_id, document_version_id, id)
        )
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_memory_documents_workspace_id ON knowledge_memory_documents(workspace_id)"
    )
    tenant = "workspace_id = nullif(current_setting('smm.workspace_id', true), '')::uuid AND public.smm_member(workspace_id) AND public.smm_knowledge_owner(workspace_id)"
    actor = "actor_id = nullif(current_setting('smm.user_id', true), '')::uuid"
    op.execute("ALTER TABLE knowledge_memory_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_memory_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY memory_read ON knowledge_memory_documents FOR SELECT USING ({tenant})"
    )
    op.execute(
        f"CREATE POLICY memory_insert ON knowledge_memory_documents FOR INSERT WITH CHECK ({tenant} AND {actor})"
    )
    op.execute("GRANT SELECT, INSERT ON knowledge_memory_documents TO smm_app")
    op.execute(
        "CREATE TRIGGER memory_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON knowledge_memory_documents FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()"
    )
    # No worker grants: curation is a personal Owner action, never an AI capability.


def downgrade() -> None:
    # Destructive provenance removal: only disposable tests or explicitly authorized rollback.
    op.execute("DROP TABLE knowledge_memory_documents")
    op.execute("ALTER TABLE knowledge_indexes DROP CONSTRAINT uq_knowledge_index_version_ref")
    op.execute("ALTER TABLE knowledge_note_reviews DROP CONSTRAINT uq_knowledge_note_review_ref")
