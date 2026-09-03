"""Owner-governed testing registry. Static snapshot; no automatic selection or paid runs."""

from alembic import op

revision = "0011_profile_registry"
down_revision = "0010_memory_curation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    boundary = """workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND public.smm_member(workspace_id) AND public.smm_knowledge_owner(workspace_id)"""
    actor = "actor_id=nullif(current_setting('smm.user_id',true),'')::uuid"
    for table in (
        "ai_profile_versions",
        "ai_profile_decisions",
        "ai_profile_heads",
        "ai_profile_receipts",
    ):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        read = boundary + (f" AND {actor}" if table == "ai_profile_receipts" else "")
        write = boundary + (f" AND {actor}" if table != "ai_profile_heads" else "")
        op.execute(f"CREATE POLICY profile_read ON {table} FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY profile_insert ON {table} FOR INSERT WITH CHECK ({write})")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO smm_app")
        if table != "ai_profile_receipts":
            op.execute(f"GRANT SELECT ON {table} TO smm_worker")
        operations = (
            "DELETE OR TRUNCATE" if table == "ai_profile_heads" else "UPDATE OR DELETE OR TRUNCATE"
        )
        op.execute(f"""CREATE TRIGGER profile_immutable BEFORE {operations} ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute(
        f"CREATE POLICY profile_update ON ai_profile_heads FOR UPDATE USING ({boundary}) WITH CHECK ({boundary})"
    )
    op.execute(
        "GRANT UPDATE (revision,latest_version_id,testing_version_id,testing_selection_id) ON ai_profile_heads TO smm_app"
    )
    op.execute(HEAD_GUARD)
    op.execute("REVOKE ALL ON FUNCTION public.smm_profile_head_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER profile_head_guard BEFORE INSERT OR UPDATE ON ai_profile_heads FOR EACH ROW EXECUTE FUNCTION public.smm_profile_head_guard()"
    )
    op.execute("""ALTER TABLE ai_runs ADD COLUMN profile_version_id uuid,
        ADD COLUMN profile_selection_id uuid,
        ADD CONSTRAINT ai_run_profile_pair CHECK ((profile_version_id IS NULL) = (profile_selection_id IS NULL)),
        ADD CONSTRAINT fk_ai_run_profile_selection FOREIGN KEY
        (workspace_id,profile,profile_version_id,profile_selection_id)
        REFERENCES ai_profile_decisions(workspace_id,profile,version_id,id)""")
    op.execute(RUN_GUARD)
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_registry_guard() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER ai_registry_guard BEFORE INSERT OR UPDATE ON ai_runs FOR EACH ROW EXECUTE FUNCTION public.smm_ai_registry_guard()"
    )


def downgrade() -> None:
    # Drops testing governance/provenance: disposable tests or an explicitly authorized restore.
    op.execute("DROP TRIGGER ai_registry_guard ON ai_runs")
    op.execute("DROP FUNCTION public.smm_ai_registry_guard()")
    op.execute(
        "ALTER TABLE ai_runs DROP CONSTRAINT fk_ai_run_profile_selection, DROP CONSTRAINT ai_run_profile_pair, DROP COLUMN profile_selection_id, DROP COLUMN profile_version_id"
    )
    op.execute("DROP TABLE ai_profile_heads")
    op.execute("DROP FUNCTION public.smm_profile_head_guard()")
    op.execute("DROP TABLE ai_profile_receipts")
    op.execute("DROP TABLE ai_profile_decisions")
    op.execute("DROP TABLE ai_profile_versions")


DDL = (
    """CREATE TABLE ai_profile_versions (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, profile varchar(32) NOT NULL, number integer NOT NULL,
        actor_id uuid NOT NULL REFERENCES users(id), provider varchar(32) NOT NULL,
        model varchar(120) NOT NULL, profile_snapshot json NOT NULL,
        execution_hash varchar(64) NOT NULL, content_hash varchar(64) NOT NULL, reason text NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,profile,id), UNIQUE(workspace_id,profile,number),
        CONSTRAINT ai_profile_number CHECK (number>=1)
    )""",
    """CREATE TABLE ai_profile_decisions (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, profile varchar(32) NOT NULL, version_id uuid NOT NULL,
        actor_id uuid NOT NULL REFERENCES users(id), action varchar(32) NOT NULL,
        revision integer NOT NULL, content_hash varchar(64) NOT NULL, reason text NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,profile,version_id,id),
        UNIQUE(workspace_id,profile,revision),
        FOREIGN KEY(workspace_id,profile,version_id) REFERENCES ai_profile_versions(workspace_id,profile,id),
        CONSTRAINT ai_profile_decision_action CHECK (action IN ('profile_select_testing','profile_disable')),
        CONSTRAINT ai_profile_decision_revision CHECK (revision>=2)
    )""",
    """CREATE TABLE ai_profile_heads (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, profile varchar(32) NOT NULL, revision integer NOT NULL,
        latest_version_id uuid NOT NULL, testing_version_id uuid, testing_selection_id uuid,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,profile),
        CONSTRAINT ai_profile_revision CHECK (revision>=1),
        CONSTRAINT ai_profile_selection_pair CHECK ((testing_version_id IS NULL) = (testing_selection_id IS NULL)),
        CONSTRAINT fk_profile_latest FOREIGN KEY(workspace_id,profile,latest_version_id)
            REFERENCES ai_profile_versions(workspace_id,profile,id),
        CONSTRAINT fk_profile_selection FOREIGN KEY(workspace_id,profile,testing_version_id,testing_selection_id)
            REFERENCES ai_profile_decisions(workspace_id,profile,version_id,id)
    )""",
    """CREATE TABLE ai_profile_receipts (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        key_hash varchar(64) NOT NULL, request_hash varchar(64) NOT NULL, result json NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,actor_id,key_hash)
    )""",
)

HEAD_GUARD = """CREATE FUNCTION public.smm_profile_head_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE latest integer; previous integer;
BEGIN
    SELECT v.number INTO latest FROM public.ai_profile_versions v
        WHERE v.workspace_id=NEW.workspace_id AND v.profile=NEW.profile AND v.id=NEW.latest_version_id;
    IF latest IS NULL THEN RAISE EXCEPTION 'profile_latest_required'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.revision<>1 OR latest<>1 OR NEW.testing_version_id IS NOT NULL
            OR NEW.testing_selection_id IS NOT NULL THEN RAISE EXCEPTION 'profile_draft_required'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.revision<>OLD.revision+1 OR
        (to_jsonb(NEW)-ARRAY['revision','latest_version_id','testing_version_id','testing_selection_id'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','latest_version_id','testing_version_id','testing_selection_id'])
        THEN RAISE EXCEPTION 'profile_revision_required'; END IF;
    IF NEW.latest_version_id<>OLD.latest_version_id THEN
        SELECT v.number INTO previous FROM public.ai_profile_versions v WHERE v.id=OLD.latest_version_id;
        IF latest<>previous+1 OR NEW.testing_version_id IS DISTINCT FROM OLD.testing_version_id
            OR NEW.testing_selection_id IS DISTINCT FROM OLD.testing_selection_id
            THEN RAISE EXCEPTION 'profile_draft_transition_invalid'; END IF;
    ELSIF NEW.testing_version_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.ai_profile_decisions d
            JOIN public.ai_profile_versions v ON v.id=d.version_id
            WHERE d.workspace_id=NEW.workspace_id AND d.profile=NEW.profile
              AND d.id=NEW.testing_selection_id AND d.version_id=NEW.testing_version_id
              AND d.revision=NEW.revision AND d.action='profile_select_testing'
              AND d.content_hash=v.content_hash AND v.profile_snapshot->>'status'='testing'
              AND v.profile_snapshot->>'blocked_reason' IS NULL)
            THEN RAISE EXCEPTION 'profile_selection_decision_required'; END IF;
    ELSE
        IF OLD.testing_version_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.ai_profile_decisions d JOIN public.ai_profile_versions v ON v.id=d.version_id
            WHERE d.workspace_id=NEW.workspace_id AND d.profile=NEW.profile
              AND d.version_id=OLD.testing_version_id AND d.revision=NEW.revision
              AND d.action='profile_disable' AND d.content_hash=v.content_hash)
            THEN RAISE EXCEPTION 'profile_disable_decision_required'; END IF;
    END IF;
    RETURN NEW;
END $$"""

RUN_GUARD = """CREATE FUNCTION public.smm_ai_registry_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
    IF NEW.state IN ('queued','running','needs_review') THEN
        IF NOT EXISTS (SELECT 1 FROM public.ai_profile_heads h
            JOIN public.ai_profile_decisions d ON d.id=h.testing_selection_id
            JOIN public.ai_profile_versions v ON v.id=h.testing_version_id
            WHERE h.workspace_id=NEW.workspace_id AND h.profile=NEW.profile
              AND h.testing_version_id=NEW.profile_version_id
              AND h.testing_selection_id=NEW.profile_selection_id
              AND d.action='profile_select_testing' AND d.content_hash=v.content_hash
              AND NEW.profile_snapshot::jsonb=v.profile_snapshot::jsonb)
            THEN RAISE EXCEPTION 'registered_testing_profile_required'; END IF;
    END IF;
    RETURN NEW;
END $$"""
