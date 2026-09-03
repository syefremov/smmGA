"""Append-only personal decisions on exact editorial findings. No content/worker writes."""

from alembic import op

revision = "0013_editor_triage"
down_revision = "0012_editor_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE editorial_decisions (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        run_id uuid NOT NULL, artifact_id uuid NOT NULL, artifact_hash varchar(64) NOT NULL,
        revision_id uuid NOT NULL, content_hash varchar(64) NOT NULL,
        finding_index integer NOT NULL, finding_hash varchar(64) NOT NULL,
        sequence integer NOT NULL, status varchar(24) NOT NULL, reason text NOT NULL,
        key_hash varchar(64) NOT NULL, request_hash varchar(64) NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,actor_id,key_hash),
        UNIQUE(workspace_id,run_id,sequence),
        FOREIGN KEY(workspace_id,run_id) REFERENCES ai_runs(workspace_id,id),
        FOREIGN KEY(workspace_id,artifact_id) REFERENCES ai_artifacts(workspace_id,id),
        FOREIGN KEY(workspace_id,revision_id) REFERENCES post_revisions(workspace_id,id),
        CONSTRAINT editorial_decision_sequence CHECK (sequence>=1),
        CONSTRAINT editorial_finding_index CHECK (finding_index BETWEEN 0 AND 19),
        CONSTRAINT editorial_status CHECK (status IN ('open','needs_changes','dismissed'))
    )""")
    op.execute("CREATE INDEX ix_editorial_decisions_workspace_id ON editorial_decisions(workspace_id)")
    op.execute("ALTER TABLE editorial_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE editorial_decisions FORCE ROW LEVEL SECURITY")
    boundary = """workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND public.smm_member(workspace_id) AND public.smm_knowledge_owner(workspace_id)
        AND EXISTS (SELECT 1 FROM public.ai_runs r WHERE r.workspace_id=editorial_decisions.workspace_id
            AND r.id=editorial_decisions.run_id AND r.actor_id=editorial_decisions.actor_id)"""
    op.execute(f"CREATE POLICY editorial_private ON editorial_decisions USING ({boundary}) WITH CHECK ({boundary})")
    op.execute("GRANT SELECT,INSERT ON editorial_decisions TO smm_app")
    op.execute("""CREATE TRIGGER editorial_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON editorial_decisions FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("""CREATE FUNCTION public.smm_editorial_decision_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended('editor-triage:'||NEW.run_id::text,0));
        IF NEW.sequence<>(SELECT coalesce(max(d.sequence),0)+1 FROM public.editorial_decisions d
            WHERE d.workspace_id=NEW.workspace_id AND d.run_id=NEW.run_id)
            THEN RAISE EXCEPTION 'editor_triage_version_conflict'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.ai_artifacts a JOIN public.ai_runs r ON r.id=a.run_id
            JOIN public.post_revisions v ON v.id=NEW.revision_id
            JOIN public.posts p ON p.id=v.post_id
            WHERE a.workspace_id=NEW.workspace_id AND a.id=NEW.artifact_id
              AND r.id=NEW.run_id AND r.actor_id=NEW.actor_id AND a.actor_id=NEW.actor_id
              AND r.profile='editor' AND r.state='needs_review'
              AND a.content_hash=NEW.artifact_hash AND a.body->>'revision_id'=v.id::text
              AND a.body->>'content_hash'=NEW.content_hash AND v.content_hash=NEW.content_hash
              AND p.current_revision_id=v.id AND p.brand_id=r.brand_id
              AND json_array_length(a.body->'findings')>NEW.finding_index)
            THEN RAISE EXCEPTION 'editor_triage_binding_invalid'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_editorial_decision_guard() FROM PUBLIC")
    op.execute("""CREATE TRIGGER editorial_decision_guard BEFORE INSERT ON editorial_decisions
        FOR EACH ROW EXECUTE FUNCTION public.smm_editorial_decision_guard()""")


def downgrade() -> None:
    # Destructive loss of human review history; only disposable/explicit restore-backed rollback.
    op.execute("DROP TABLE editorial_decisions")
    op.execute("DROP FUNCTION public.smm_editorial_decision_guard()")
