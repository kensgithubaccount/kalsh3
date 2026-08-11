-- M13 deterministic next-gate risk state. No exchange mutation or signer material.
CREATE TABLE risk_policy_versions (
    policy_id text NOT NULL, version text NOT NULL, effective_at timestamptz NOT NULL,
    predecessor text, starting_bankroll numeric NOT NULL CHECK(starting_bankroll=1000),
    protected_reserve numeric NOT NULL CHECK(protected_reserve>=700),
    active_capital_limit numeric NOT NULL CHECK(active_capital_limit<=300),
    aggregate_open_risk_limit numeric NOT NULL CHECK(aggregate_open_risk_limit<=100),
    per_market_risk_limit numeric NOT NULL CHECK(per_market_risk_limit<=10),
    related_event_risk_limit numeric NOT NULL CHECK(related_event_risk_limit<=25),
    daily_loss_stop numeric NOT NULL CHECK(daily_loss_stop<=20),
    weekly_loss_stop numeric NOT NULL CHECK(weekly_loss_stop<=50),
    monthly_loss_stop numeric NOT NULL CHECK(monthly_loss_stop<=100),
    experiment_drawdown_stop numeric NOT NULL CHECK(experiment_drawdown_stop<=200),
    risk_timezone text NOT NULL DEFAULT 'America/New_York', code_sha text NOT NULL,
    content_hash text NOT NULL, PRIMARY KEY(policy_id,version)
);
CREATE TABLE experiment_ledger_entries (
    entry_id text PRIMARY KEY, happened_at timestamptz NOT NULL, entry_type text NOT NULL,
    amount numeric NOT NULL, ownership text NOT NULL CHECK(ownership IN ('BOT_OWNED','EXTERNAL_KNOWN','EXTERNAL_UNKNOWN')),
    source_reference text NOT NULL, correction_of text REFERENCES experiment_ledger_entries(entry_id),
    simulation boolean NOT NULL DEFAULT false
);
CREATE TABLE experiment_equity_snapshots (
    snapshot_id text PRIMARY KEY, observed_at timestamptz NOT NULL, experiment_equity numeric NOT NULL,
    high_water_mark numeric NOT NULL, drawdown numeric NOT NULL CHECK(drawdown>=0),
    trigger_lineage jsonb NOT NULL, content_hash text NOT NULL
);
CREATE TABLE external_account_activity (
    activity_id text PRIMARY KEY, observed_at timestamptz NOT NULL, subaccount integer NOT NULL CHECK(subaccount=0),
    activity_type text NOT NULL, ownership text NOT NULL, acknowledged boolean NOT NULL,
    safe_summary jsonb NOT NULL
);
CREATE TABLE reconciliation_runs (
    reconciliation_id text PRIMARY KEY, observed_at timestamptz NOT NULL,
    subaccount integer NOT NULL CHECK(subaccount=0), status text NOT NULL CHECK(status IN
    ('RECONCILED','STALE','PARTIAL','MISMATCH','UNKNOWN_ORDER','UNKNOWN_POSITION','AUTH_FAILURE','API_FAILURE')),
    balance_complete boolean NOT NULL, positions_complete boolean NOT NULL,
    orders_complete boolean NOT NULL, fills_complete boolean NOT NULL,
    settlements_complete boolean NOT NULL, ledger_complete boolean NOT NULL,
    content_hash text NOT NULL
);
CREATE TABLE reconciliation_issues (
    reconciliation_id text NOT NULL REFERENCES reconciliation_runs(reconciliation_id),
    reason_code text NOT NULL, safe_detail text NOT NULL,
    PRIMARY KEY(reconciliation_id,reason_code)
);
CREATE TABLE portfolio_risk_snapshots (
    snapshot_id text PRIMARY KEY, observed_at timestamptz NOT NULL,
    reconciliation_id text NOT NULL REFERENCES reconciliation_runs(reconciliation_id),
    cash numeric, account_equity numeric, protected_reserve numeric NOT NULL,
    active_capital_available numeric, current_market_risk numeric, current_event_risk numeric,
    current_aggregate_risk numeric, resting_order_potential_risk numeric,
    projected_market_risk numeric, projected_event_risk numeric, projected_aggregate_risk numeric,
    realized_daily_pnl numeric, realized_weekly_pnl numeric, realized_monthly_pnl numeric,
    experiment_equity numeric, experiment_high_water_mark numeric, experiment_drawdown numeric,
    unknown_orders integer NOT NULL, unknown_positions integer NOT NULL,
    content_hash text NOT NULL
);
CREATE TABLE loss_window_states (
    state_id text PRIMARY KEY, as_of timestamptz NOT NULL, risk_date date NOT NULL,
    week_start date NOT NULL, month_start date NOT NULL, daily_loss numeric NOT NULL,
    weekly_loss numeric NOT NULL, monthly_loss numeric NOT NULL, daily_triggered_at timestamptz,
    weekly_review_required boolean NOT NULL, monthly_review_required boolean NOT NULL,
    content_hash text NOT NULL
);
CREATE TABLE drawdown_states (
    state_id text PRIMARY KEY, as_of timestamptz NOT NULL, experiment_equity numeric NOT NULL,
    high_water_mark numeric NOT NULL, drawdown numeric NOT NULL,
    halt_required boolean NOT NULL, content_hash text NOT NULL
);
CREATE TABLE kill_states (
    category text PRIMARY KEY CHECK(category IN ('STRATEGY','DATA','PORTFOLIO','CREDENTIAL')),
    level text NOT NULL CHECK(level IN ('NORMAL','WARNING','KILLED')),
    reason text NOT NULL, changed_at timestamptz NOT NULL
);
CREATE TABLE compliance_holds (
    hold_id text PRIMARY KEY, state text NOT NULL CHECK(state IN ('CLEAR','HOLD','UNKNOWN')),
    reason text NOT NULL, actor text NOT NULL, changed_at timestamptz NOT NULL, content_hash text NOT NULL
);
CREATE TABLE global_halts (
    halt_id text PRIMARY KEY, active boolean NOT NULL, reason text NOT NULL, actor text NOT NULL,
    changed_at timestamptz NOT NULL, reset_requires_strong_reauth boolean NOT NULL DEFAULT true,
    content_hash text NOT NULL
);
CREATE TABLE required_order_group_policies (
    policy_id text PRIMARY KEY, required boolean NOT NULL, expected_subaccount integer NOT NULL CHECK(expected_subaccount=0),
    contract_limit_ceiling numeric NOT NULL, group_health_required boolean NOT NULL,
    content_hash text NOT NULL
);
CREATE TABLE risk_intents (
    intent_id text PRIMARY KEY, created_at timestamptz NOT NULL, market_ticker text NOT NULL,
    event_id text NOT NULL, correlation_cluster_id text NOT NULL, economic_action text NOT NULL,
    price numeric NOT NULL, quantity numeric NOT NULL, maximum_expected_fee numeric NOT NULL,
    maximum_expected_cash_commitment numeric NOT NULL, maximum_loss_if_filled numeric NOT NULL,
    client_order_id text NOT NULL UNIQUE, subaccount integer NOT NULL CHECK(subaccount=0),
    rules_hash text NOT NULL, candidate_id text NOT NULL, intent jsonb NOT NULL, content_hash text NOT NULL
);
CREATE TABLE client_order_id_registry (
    client_order_id text PRIMARY KEY, intent_id text NOT NULL REFERENCES risk_intents(intent_id),
    intent_hash text NOT NULL, registered_at timestamptz NOT NULL
);
CREATE TABLE risk_decisions (
    decision_id text PRIMARY KEY, intent_id text NOT NULL REFERENCES risk_intents(intent_id),
    portfolio_snapshot_id text NOT NULL REFERENCES portfolio_risk_snapshots(snapshot_id),
    policy_id text NOT NULL, policy_version text NOT NULL, decided_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL, state text NOT NULL CHECK(state IN ('PASS_NEXT_GATE','REJECT','PAUSE','HALT')),
    display_result text NOT NULL CHECK(display_result IN ('RISK CHECK PASSED','RISK CHECK FAILED')),
    production_write_authorized boolean NOT NULL DEFAULT false CHECK(NOT production_write_authorized),
    content_hash text NOT NULL
);
CREATE TABLE risk_decision_reasons (
    decision_id text NOT NULL REFERENCES risk_decisions(decision_id), reason_code text NOT NULL,
    PRIMARY KEY(decision_id,reason_code)
);
CREATE TABLE risk_authorizations (
    authorization_id text PRIMARY KEY, decision_id text NOT NULL REFERENCES risk_decisions(decision_id),
    intent_hash text NOT NULL, portfolio_state_hash text NOT NULL, policy_version text NOT NULL,
    rules_version text NOT NULL, safety_state_hash text NOT NULL, created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL, state text NOT NULL CHECK(state IN ('ISSUED','CONSUMED','EXPIRED','REVOKED')),
    production_execution_authorized boolean NOT NULL DEFAULT false CHECK(NOT production_execution_authorized)
);
CREATE TABLE risk_reservations (
    authorization_id text PRIMARY KEY REFERENCES risk_authorizations(authorization_id),
    market_ticker text NOT NULL, event_id text NOT NULL, market_risk numeric NOT NULL,
    event_risk numeric NOT NULL, aggregate_risk numeric NOT NULL, cash_commitment numeric NOT NULL,
    expires_at timestamptz NOT NULL, active boolean NOT NULL
);
CREATE TABLE risk_events (
    event_id text PRIMARY KEY, event_type text NOT NULL, actor text NOT NULL,
    happened_at timestamptz NOT NULL, reason text NOT NULL, policy_version text,
    state_hash text, safe_evidence jsonb NOT NULL, content_hash text NOT NULL
);
