"""Conservative immutable AI reservations and usage estimates, not provider invoices."""

from alembic import op

revision = "0019_ai_costs"
down_revision = "0018_text_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE ai_cost_reservations (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        run_id uuid NOT NULL, input_hash varchar(64) NOT NULL, policy_hash varchar(64) NOT NULL,
        policy json NOT NULL, reserved_microusd bigint NOT NULL,
        UNIQUE(workspace_id,id), UNIQUE(workspace_id,run_id),
        FOREIGN KEY(workspace_id,run_id) REFERENCES ai_runs(workspace_id,id),
        CONSTRAINT cost_reserve_amount CHECK(reserved_microusd>0 AND reserved_microusd<=1000000000)
    )""")
    op.execute("""CREATE TABLE ai_cost_observations (
        id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
        created_at timestamptz NOT NULL, actor_id uuid NOT NULL REFERENCES users(id),
        run_id uuid NOT NULL, lease_id uuid NOT NULL,
        input_tokens bigint NOT NULL, output_tokens bigint NOT NULL,
        estimated_microusd bigint NOT NULL, model varchar(120) NOT NULL,
        response_id varchar(160) NOT NULL, UNIQUE(workspace_id,id), UNIQUE(workspace_id,run_id),
        FOREIGN KEY(workspace_id,run_id) REFERENCES ai_runs(workspace_id,id),
        CONSTRAINT cost_usage_amount CHECK(input_tokens BETWEEN 0 AND 1000000000
            AND output_tokens BETWEEN 0 AND 1000000000 AND estimated_microusd>=0)
    )""")
    for table in ("ai_cost_reservations", "ai_cost_observations"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY cost_private ON {table} USING (
            workspace_id=nullif(current_setting('smm.workspace_id',true),'')::uuid
            AND actor_id=nullif(current_setting('smm.user_id',true),'')::uuid
            AND public.smm_knowledge_owner(workspace_id))""")
        op.execute(f"GRANT SELECT ON {table} TO smm_app,smm_worker")
        op.execute(f"""CREATE TRIGGER cost_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.smm_audit_immutable()""")
    op.execute("GRANT INSERT ON ai_cost_reservations TO smm_app")
    op.execute("GRANT INSERT ON ai_cost_observations TO smm_worker")
    op.execute("""CREATE FUNCTION public.smm_ai_cost_totals(w uuid)
        RETURNS TABLE(reserved_microusd bigint,estimated_microusd bigint,
                      unresolved_runs bigint,overrun_runs bigint,in_flight_runs bigint)
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
        IF w IS DISTINCT FROM nullif(current_setting('smm.workspace_id',true),'')::uuid
            OR NOT public.smm_knowledge_owner(w) THEN RAISE EXCEPTION 'access_denied'; END IF;
        RETURN QUERY SELECT
            (SELECT coalesce(sum(c.reserved_microusd),0)::bigint
                FROM public.ai_cost_reservations c WHERE c.workspace_id=w),
            (SELECT coalesce(sum(o.estimated_microusd),0)::bigint
                FROM public.ai_cost_observations o WHERE o.workspace_id=w),
            (SELECT count(*) FROM public.ai_runs r LEFT JOIN public.ai_cost_observations o
                ON o.workspace_id=r.workspace_id AND o.run_id=r.id
             WHERE r.workspace_id=w AND o.id IS NULL
                AND r.state NOT IN ('queued','running','cancel_requested')
                AND (r.usage->>'attempts'='1' OR r.started_at IS NOT NULL
                     OR r.state IN ('unknown','needs_review')
                     OR r.usage->>'input_tokens' IS NOT NULL)),
            (SELECT count(*) FROM public.ai_cost_observations o JOIN public.ai_cost_reservations c
                ON c.workspace_id=o.workspace_id AND c.run_id=o.run_id
             WHERE o.workspace_id=w AND o.estimated_microusd>c.reserved_microusd),
            (SELECT count(*) FROM public.ai_runs r WHERE r.workspace_id=w
                AND r.state IN ('running','cancel_requested'));
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.smm_ai_cost_totals(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.smm_ai_cost_totals(uuid) TO smm_app,smm_worker")
    op.execute("""CREATE FUNCTION public.smm_ai_cost_reserve_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE used bigint; lim bigint; BEGIN
        PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to(
            'knowledge:'||NEW.workspace_id::text,'UTF8')),'hex'),1,15))::bit(60)::bigint);
        IF NOT EXISTS (SELECT 1 FROM public.ai_runs r JOIN public.ai_inputs i
            ON i.workspace_id=r.workspace_id AND i.run_id=r.id
            WHERE r.workspace_id=NEW.workspace_id AND r.id=NEW.run_id
                AND r.actor_id=NEW.actor_id AND i.actor_id=NEW.actor_id AND r.state='queued'
                AND r.provider='openai' AND r.model=NEW.policy->>'model'
                AND i.content_hash=NEW.input_hash) THEN
            RAISE EXCEPTION 'cost_input_mismatch'; END IF;
        lim := (NEW.policy->>'workspace_limit_microusd')::bigint;
        IF lim IS NULL OR lim NOT BETWEEN 1 AND 1000000000000
            OR NEW.reserved_microusd IS DISTINCT FROM (NEW.policy->>'reserve_microusd')::bigint
            OR NEW.policy->>'currency' IS DISTINCT FROM 'USD'
            THEN RAISE EXCEPTION 'cost_policy_invalid'; END IF;
        SELECT t.reserved_microusd INTO used FROM public.smm_ai_cost_totals(NEW.workspace_id) t;
        IF used+NEW.reserved_microusd>lim THEN RAISE EXCEPTION 'ai_budget_exhausted'; END IF;
        RETURN NEW; END $$""")
    op.execute("""CREATE FUNCTION public.smm_ai_cost_dispatch_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
        IF NEW.state='running' AND OLD.state='queued' AND NOT EXISTS (
            SELECT 1 FROM public.ai_cost_reservations c JOIN public.ai_inputs i
            ON i.workspace_id=c.workspace_id AND i.run_id=c.run_id
            WHERE c.workspace_id=NEW.workspace_id AND c.run_id=NEW.id
                AND c.actor_id=NEW.actor_id AND c.input_hash=i.content_hash)
            THEN RAISE EXCEPTION 'ai_cost_reservation_required'; END IF;
        RETURN NEW; END $$""")
    op.execute("""CREATE FUNCTION public.smm_ai_cost_observe_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE p json; expected bigint; BEGIN
        SELECT c.policy INTO p FROM public.ai_cost_reservations c JOIN public.ai_runs r
            ON r.workspace_id=c.workspace_id AND r.id=c.run_id
            WHERE c.workspace_id=NEW.workspace_id AND c.run_id=NEW.run_id
                AND c.actor_id=NEW.actor_id AND r.actor_id=NEW.actor_id
                AND r.lease_id=NEW.lease_id AND r.lease_until>clock_timestamp()
                AND r.state IN ('running','cancel_requested');
        IF p IS NULL OR p->>'model' IS DISTINCT FROM NEW.model THEN
            RAISE EXCEPTION 'cost_observation_fenced'; END IF;
        expected := ceil((NEW.input_tokens::numeric*(p->>'input_rate_microusd_per_million')::numeric
            + NEW.output_tokens::numeric*(p->>'output_rate_microusd_per_million')::numeric)
            /1000000)::bigint;
        IF expected IS NULL OR NEW.estimated_microusd<>expected THEN
            RAISE EXCEPTION 'cost_estimate_mismatch'; END IF;
        RETURN NEW; END $$""")
    for name, target, event in (
        ("reserve", "ai_cost_reservations", "INSERT"),
        ("dispatch", "ai_runs", "UPDATE"),
        ("observe", "ai_cost_observations", "INSERT"),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.smm_ai_cost_{name}_guard() FROM PUBLIC")
        op.execute(f"""CREATE TRIGGER ai_cost_{name}_guard BEFORE {event} ON {target}
            FOR EACH ROW EXECUTE FUNCTION public.smm_ai_cost_{name}_guard()""")


def downgrade() -> None:
    op.execute("SET LOCAL row_security=off")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM ai_cost_reservations)
        OR EXISTS(SELECT 1 FROM ai_cost_observations) THEN
        RAISE EXCEPTION 'ai_cost_history_requires_restore_plan'; END IF; END $$""")
    for name, target in (
        ("reserve", "ai_cost_reservations"),
        ("observe", "ai_cost_observations"),
        ("dispatch", "ai_runs"),
    ):
        op.execute(f"DROP TRIGGER ai_cost_{name}_guard ON {target}")
        op.execute(f"DROP FUNCTION public.smm_ai_cost_{name}_guard()")
    op.execute("DROP FUNCTION public.smm_ai_cost_totals(uuid)")
    op.execute("DROP TABLE ai_cost_observations,ai_cost_reservations")
