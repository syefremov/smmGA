"""Immutable human transfer receipt; no worker capability or automatic adoption."""

from alembic import op

revision = "0015_copy_adoption"
down_revision = "0014_copywriter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE copy_adoptions (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        run_id uuid NOT NULL, artifact_id uuid NOT NULL, artifact_hash varchar(64) NOT NULL,
        input_id uuid NOT NULL, input_hash varchar(64) NOT NULL, post_id uuid NOT NULL,
        source_revision_id uuid NOT NULL, source_content_hash varchar(64) NOT NULL,
        revision_id uuid NOT NULL, content_hash varchar(64) NOT NULL, post_version integer NOT NULL,
        preview_hash varchar(64) NOT NULL, reason text NOT NULL, preflight json NOT NULL,
        key_hash varchar(64) NOT NULL, request_hash varchar(64) NOT NULL,
        human_confirmed boolean NOT NULL, share_with_workspace_confirmed boolean NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,run_id), UNIQUE(workspace_id,revision_id),
        UNIQUE(workspace_id,actor_id,key_hash),
        FOREIGN KEY(workspace_id,run_id) REFERENCES ai_runs(workspace_id,id),
        FOREIGN KEY(workspace_id,artifact_id) REFERENCES ai_artifacts(workspace_id,id),
        FOREIGN KEY(workspace_id,input_id) REFERENCES ai_inputs(workspace_id,id),
        FOREIGN KEY(workspace_id,post_id) REFERENCES posts(workspace_id,id),
        FOREIGN KEY(workspace_id,post_id,source_revision_id) REFERENCES post_revisions(workspace_id,post_id,id),
        FOREIGN KEY(workspace_id,post_id,revision_id) REFERENCES post_revisions(workspace_id,post_id,id),
        CONSTRAINT copy_adoption_revision CHECK (source_revision_id<>revision_id AND post_version>=2),
        CONSTRAINT copy_adoption_confirmation CHECK (human_confirmed AND share_with_workspace_confirmed)
    )""")
    op.execute("CREATE INDEX ix_copy_adoptions_workspace_id ON copy_adoptions(workspace_id)")
    op.execute("ALTER TABLE copy_adoptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE copy_adoptions FORCE ROW LEVEL SECURITY")
    boundary = """workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND public.smm_member(workspace_id) AND public.smm_knowledge_owner(workspace_id)
        AND EXISTS (SELECT 1 FROM public.ai_runs r WHERE r.workspace_id=copy_adoptions.workspace_id
            AND r.id=copy_adoptions.run_id AND r.actor_id=copy_adoptions.actor_id)"""
    op.execute(f"CREATE POLICY copy_adoption_private ON copy_adoptions USING ({boundary}) WITH CHECK ({boundary})")
    op.execute("GRANT SELECT,INSERT ON copy_adoptions TO smm_app")
    op.execute("""CREATE TRIGGER copy_adoption_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON copy_adoptions FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("""CREATE FUNCTION public.smm_copy_adoption_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM public.ai_runs r
            JOIN public.ai_artifacts a ON a.workspace_id=r.workspace_id AND a.run_id=r.id
            JOIN public.ai_inputs i ON i.workspace_id=r.workspace_id AND i.run_id=r.id
            JOIN public.ai_profile_heads h ON h.workspace_id=r.workspace_id AND h.profile=r.profile
            JOIN public.posts p ON p.workspace_id=r.workspace_id AND p.id=NEW.post_id
            JOIN public.post_revisions src ON src.workspace_id=p.workspace_id AND src.post_id=p.id
                AND src.id=NEW.source_revision_id
            JOIN public.post_revisions v ON v.workspace_id=p.workspace_id AND v.post_id=p.id
                AND v.id=NEW.revision_id
            WHERE r.workspace_id=NEW.workspace_id AND r.id=NEW.run_id AND r.actor_id=NEW.actor_id
                AND r.profile='copywriter' AND r.state='needs_review' AND p.brand_id=r.brand_id
                AND h.testing_version_id=r.profile_version_id AND h.testing_selection_id=r.profile_selection_id
                AND a.id=NEW.artifact_id AND a.actor_id=NEW.actor_id AND a.content_hash=NEW.artifact_hash
                AND a.body->>'outcome'='draft' AND a.body->>'revision_id'=src.id::text
                AND a.body->>'content_hash'=src.content_hash AND src.content_hash=NEW.source_content_hash
                AND i.id=NEW.input_id AND i.content_hash=NEW.input_hash AND i.actor_id=NEW.actor_id
                AND i.post_id=p.id AND i.revision_id=src.id AND i.copy_context IS NOT NULL
                AND v.content_hash=NEW.content_hash AND v.actor_id=NEW.actor_id AND v.number=src.number+1
                AND p.current_revision_id=v.id AND p.revision_count=v.number AND p.version=NEW.post_version
                AND p.state='draft' AND p.active_approval_id IS NULL AND v.media_manifest::jsonb='[]'::jsonb
                AND v.body::jsonb->'fact_ids'=src.body::jsonb->'fact_ids'
                AND v.body::jsonb->'knowledge_gaps'=a.body::jsonb->'knowledge_gaps'
                AND v.body::jsonb->'variants'=(
                    SELECT jsonb_agg(jsonb_set(original,'{text}',variant->'text') ORDER BY idx)
                    FROM jsonb_array_elements(src.body::jsonb->'variants') WITH ORDINALITY AS originals(original,idx)
                    JOIN jsonb_array_elements(a.body::jsonb->'variants') AS drafts(variant)
                        ON (variant->>'variant_index')::integer=idx-1)
                AND NEW.preflight->>'revision_id'=v.id::text
                AND NEW.preflight->>'content_hash'=v.content_hash
        ) THEN RAISE EXCEPTION 'copy_adoption_binding_invalid'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_copy_adoption_guard() FROM PUBLIC")
    op.execute("""CREATE TRIGGER copy_adoption_guard BEFORE INSERT ON copy_adoptions
        FOR EACH ROW EXECUTE FUNCTION public.smm_copy_adoption_guard()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM copy_adoptions)
        THEN RAISE EXCEPTION 'copy_adoption_history_requires_restore_plan'; END IF; END $$""")
    op.execute("DROP TABLE copy_adoptions")
    op.execute("DROP FUNCTION public.smm_copy_adoption_guard()")
