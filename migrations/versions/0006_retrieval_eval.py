"""Append-only retrieval benchmarks. Static migration; never import live ORM models."""

from alembic import op

revision = "0006_retrieval_eval"
down_revision = "0005_knowledge"
branch_labels = None
depends_on = None

TABLES = [
    "retrieval_eval_receipts",
    "retrieval_eval_datasets",
    "retrieval_eval_runs",
    "retrieval_eval_reviews",
]


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    tenant = "workspace_id = nullif(current_setting('smm.workspace_id', true), '')::uuid AND public.smm_member(workspace_id) AND public.smm_knowledge_owner(workspace_id)"
    actor = "actor_id = nullif(current_setting('smm.user_id', true), '')::uuid"
    for table in TABLES:
        read = tenant + (" AND " + actor if table == "retrieval_eval_receipts" else "")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY eval_read ON {table} FOR SELECT USING ({read})")
        op.execute(
            f"CREATE POLICY eval_insert ON {table} FOR INSERT WITH CHECK ({tenant} AND {actor})"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO smm_app")
        op.execute(
            f"CREATE TRIGGER eval_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()"
        )
    # No worker grants; a background ingestion worker cannot read private eval queries/reports.


def downgrade() -> None:
    # Destructive: disposable tests or separately authorized restore-backed rollback only.
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")


DDL = (
    """
CREATE TABLE retrieval_eval_receipts (
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
CREATE TABLE retrieval_eval_datasets (
	brand_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	family_id UUID NOT NULL,
	number INTEGER NOT NULL,
	previous_dataset_id UUID,
	content_hash VARCHAR(64) NOT NULL,
	definition JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	FOREIGN KEY(workspace_id, previous_dataset_id) REFERENCES retrieval_eval_datasets (workspace_id, id),
	UNIQUE (workspace_id, family_id, number),
	UNIQUE (workspace_id, brand_id, id, content_hash),
	UNIQUE (workspace_id, brand_id, family_id, id),
	FOREIGN KEY(workspace_id, brand_id, family_id, previous_dataset_id) REFERENCES retrieval_eval_datasets (workspace_id, brand_id, family_id, id),
	CONSTRAINT retrieval_eval_dataset_number CHECK (number >= 1),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE retrieval_eval_runs (
	brand_id UUID NOT NULL,
	actor_id UUID NOT NULL,
	dataset_id UUID NOT NULL,
	dataset_hash VARCHAR(64) NOT NULL,
	corpus_hash VARCHAR(64) NOT NULL,
	report_hash VARCHAR(64) NOT NULL,
	corpus JSON NOT NULL,
	report JSON NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id) REFERENCES brands (workspace_id, id),
	FOREIGN KEY(workspace_id, dataset_id) REFERENCES retrieval_eval_datasets (workspace_id, id),
	FOREIGN KEY(workspace_id, brand_id, dataset_id, dataset_hash) REFERENCES retrieval_eval_datasets (workspace_id, brand_id, id, content_hash),
	UNIQUE (workspace_id, id, report_hash),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE TABLE retrieval_eval_reviews (
	actor_id UUID NOT NULL,
	run_id UUID NOT NULL,
	report_hash VARCHAR(64) NOT NULL,
	decision VARCHAR(24) NOT NULL,
	reason TEXT NOT NULL,
	workspace_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (workspace_id, id),
	FOREIGN KEY(workspace_id, run_id) REFERENCES retrieval_eval_runs (workspace_id, id),
	UNIQUE (workspace_id, run_id),
	FOREIGN KEY(workspace_id, run_id, report_hash) REFERENCES retrieval_eval_runs (workspace_id, id, report_hash),
	CONSTRAINT retrieval_eval_decision CHECK (decision IN ('accept_baseline','reject')),
	FOREIGN KEY(actor_id) REFERENCES users (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
)
    """,
    """
CREATE INDEX ix_retrieval_eval_receipts_workspace_id ON retrieval_eval_receipts (workspace_id)
    """,
    """
CREATE INDEX ix_retrieval_eval_datasets_workspace_id ON retrieval_eval_datasets (workspace_id)
    """,
    """
CREATE INDEX ix_retrieval_eval_runs_workspace_id ON retrieval_eval_runs (workspace_id)
    """,
    """
CREATE INDEX ix_retrieval_eval_reviews_workspace_id ON retrieval_eval_reviews (workspace_id)
    """,
)
