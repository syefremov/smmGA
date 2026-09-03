"""Exact human planning transfer, shared notes and private immutable receipt."""

from alembic import op

revision = "0017_plan_adoption"
down_revision = "0016_planner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE plan_notes (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        plan_id uuid NOT NULL, plan_hash varchar(64) NOT NULL, content_hash varchar(64) NOT NULL,
        body json NOT NULL, UNIQUE(workspace_id,id), UNIQUE(workspace_id,plan_id),
        FOREIGN KEY(workspace_id,plan_id) REFERENCES content_records(workspace_id,id)
    )""")
    op.execute("""CREATE TABLE plan_adoptions (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        run_id uuid NOT NULL, artifact_id uuid NOT NULL, artifact_hash varchar(64) NOT NULL,
        input_id uuid NOT NULL, input_hash varchar(64) NOT NULL,
        source_plan_id uuid NOT NULL, source_content_hash varchar(64) NOT NULL,
        plan_id uuid NOT NULL, content_hash varchar(64) NOT NULL, plan_number integer NOT NULL,
        notes_id uuid NOT NULL, notes_hash varchar(64) NOT NULL, preview_hash varchar(64) NOT NULL,
        reason text NOT NULL, key_hash varchar(64) NOT NULL, request_hash varchar(64) NOT NULL,
        human_confirmed boolean NOT NULL, share_with_workspace_confirmed boolean NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,run_id), UNIQUE(workspace_id,plan_id),
        UNIQUE(workspace_id,notes_id), UNIQUE(workspace_id,actor_id,key_hash),
        FOREIGN KEY(workspace_id,run_id) REFERENCES ai_runs(workspace_id,id),
        FOREIGN KEY(workspace_id,artifact_id) REFERENCES ai_artifacts(workspace_id,id),
        FOREIGN KEY(workspace_id,input_id) REFERENCES ai_inputs(workspace_id,id),
        FOREIGN KEY(workspace_id,source_plan_id) REFERENCES content_records(workspace_id,id),
        FOREIGN KEY(workspace_id,plan_id) REFERENCES content_records(workspace_id,id),
        FOREIGN KEY(workspace_id,notes_id) REFERENCES plan_notes(workspace_id,id),
        CONSTRAINT plan_adoption_version CHECK (source_plan_id<>plan_id AND plan_number>=2),
        CONSTRAINT plan_adoption_confirmation
            CHECK (human_confirmed AND share_with_workspace_confirmed)
    )""")
    for table in ("plan_notes", "plan_adoptions"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO smm_app")
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    workspace = """workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
        AND public.smm_member(workspace_id)"""
    owner = """actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
        AND public.smm_knowledge_owner(workspace_id)"""
    op.execute(f"CREATE POLICY plan_notes_read ON plan_notes FOR SELECT USING ({workspace})")
    op.execute(f"""CREATE POLICY plan_notes_write ON plan_notes FOR INSERT
        WITH CHECK ({workspace} AND {owner})""")
    boundary = f"""{workspace} AND {owner}
        AND EXISTS (SELECT 1 FROM public.ai_runs r WHERE r.workspace_id=plan_adoptions.workspace_id
            AND r.id=plan_adoptions.run_id AND r.actor_id=plan_adoptions.actor_id)"""
    op.execute(f"""CREATE POLICY plan_adoption_private ON plan_adoptions
        USING ({boundary}) WITH CHECK ({boundary})""")
    op.execute("""CREATE FUNCTION public.smm_plan_adoption_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM public.ai_runs r
            JOIN public.ai_artifacts a ON a.workspace_id=r.workspace_id AND a.run_id=r.id
            JOIN public.ai_inputs i ON i.workspace_id=r.workspace_id AND i.run_id=r.id
            JOIN public.ai_profile_heads h ON h.workspace_id=r.workspace_id AND h.profile=r.profile
            JOIN public.content_records src ON src.workspace_id=r.workspace_id
                AND src.id=NEW.source_plan_id
            JOIN public.content_records v ON v.workspace_id=r.workspace_id AND v.id=NEW.plan_id
            JOIN public.plan_notes n ON n.workspace_id=r.workspace_id AND n.id=NEW.notes_id
            WHERE r.workspace_id=NEW.workspace_id AND r.id=NEW.run_id AND r.actor_id=NEW.actor_id
                AND r.profile='content_planner' AND r.state='needs_review'
                AND h.testing_version_id=r.profile_version_id
                AND h.testing_selection_id=r.profile_selection_id
                AND a.id=NEW.artifact_id AND a.actor_id=NEW.actor_id
                AND a.content_hash=NEW.artifact_hash
                AND a.body->>'outcome'='draft' AND a.body->>'plan_id'=src.id::text
                AND a.body->>'content_hash'=src.content_hash
                AND src.content_hash=NEW.source_content_hash
                AND i.id=NEW.input_id AND i.content_hash=NEW.input_hash AND i.actor_id=NEW.actor_id
                AND i.plan_id=src.id AND i.planner_context IS NOT NULL
                AND src.kind='content_plan' AND v.kind=src.kind AND src.brand_id=r.brand_id
                AND v.brand_id=src.brand_id AND v.family_id=src.family_id AND v.number=src.number+1
                AND v.number=NEW.plan_number AND v.actor_id=NEW.actor_id AND v.confirmed_by IS NULL
                AND v.content_hash=NEW.content_hash AND v.expires_at=src.expires_at
                AND v.number=(SELECT max(number) FROM public.content_records
                    WHERE workspace_id=v.workspace_id AND family_id=v.family_id)
                AND (v.body::jsonb-'slots')=(src.body::jsonb-'slots')
                AND v.body::jsonb->'slots'=(
                    SELECT jsonb_agg(jsonb_set(original,'{topic}',slot->'topic') ORDER BY idx)
                    FROM jsonb_array_elements(src.body::jsonb->'slots')
                        WITH ORDINALITY AS originals(original,idx)
                    JOIN jsonb_array_elements(a.body::jsonb->'slots') AS drafts(slot)
                        ON (slot->>'slot_index')::integer=idx-1)
                AND n.plan_id=v.id AND n.plan_hash=v.content_hash AND n.content_hash=NEW.notes_hash
                AND n.actor_id=NEW.actor_id AND n.body::jsonb=jsonb_build_object(
                    'fact_ids',i.planner_context::jsonb->'fact_ids',
                    'evidence_record_ids',(SELECT jsonb_agg(rec->>'id' ORDER BY rec->>'id')
                        FROM jsonb_array_elements(i.planner_context::jsonb->'records')
                            AS records(rec)),
                    'slots',(SELECT jsonb_agg(slot ORDER BY (slot->>'slot_index')::integer)
                        FROM jsonb_array_elements(a.body::jsonb->'slots') AS slots(slot)),
                    'warnings',a.body::jsonb->'warnings','knowledge_gaps',a.body::jsonb->'knowledge_gaps')
        ) THEN RAISE EXCEPTION 'plan_adoption_binding_invalid'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_plan_adoption_guard() FROM PUBLIC")
    op.execute("""CREATE TRIGGER plan_adoption_guard BEFORE INSERT ON plan_adoptions
        FOR EACH ROW EXECUTE FUNCTION public.smm_plan_adoption_guard()""")
    # The application inserts the shared notes before their FK-dependent private receipt.
    # A standalone or rolled-back half-transfer must never commit visible notes.
    op.execute("""CREATE FUNCTION public.smm_plan_notes_receipt_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.plan_adoptions a
            WHERE a.workspace_id=NEW.workspace_id AND a.notes_id=NEW.id
                AND a.plan_id=NEW.plan_id AND a.actor_id=NEW.actor_id
                AND a.notes_hash=NEW.content_hash AND a.content_hash=NEW.plan_hash)
        THEN RAISE EXCEPTION 'plan_notes_receipt_required'; END IF;
        RETURN NEW; END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_plan_notes_receipt_guard() FROM PUBLIC")
    op.execute("""CREATE CONSTRAINT TRIGGER plan_notes_receipt_guard AFTER INSERT ON plan_notes
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION public.smm_plan_notes_receipt_guard()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM plan_adoptions)
        OR EXISTS (SELECT 1 FROM plan_notes)
        THEN RAISE EXCEPTION 'plan_adoption_history_requires_restore_plan'; END IF; END $$""")
    op.execute("DROP TABLE plan_adoptions")
    op.execute("DROP TABLE plan_notes")
    op.execute("DROP FUNCTION public.smm_plan_adoption_guard()")
    op.execute("DROP FUNCTION public.smm_plan_notes_receipt_guard()")
