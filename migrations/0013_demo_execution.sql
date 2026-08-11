-- M14 mock/paper/demo execution. Private key material is deliberately absent.
CREATE TYPE execution_mode AS ENUM ('MOCK','PAPER','DEMO');
CREATE TABLE risk_capacity_lock(singleton smallint PRIMARY KEY CHECK(singleton=1));
INSERT INTO risk_capacity_lock VALUES(1);
CREATE TABLE execution_submission_claims(
 authorization_id text PRIMARY KEY REFERENCES risk_authorizations(authorization_id),
 client_order_id text NOT NULL UNIQUE, execution_id text NOT NULL UNIQUE,
 claimed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE execution_journals(
 execution_id text PRIMARY KEY, intent_hash text NOT NULL,
 client_order_id text NOT NULL UNIQUE, authorization_id text NOT NULL UNIQUE,
 mode execution_mode NOT NULL, host text NOT NULL,
 method text NOT NULL CHECK(method IN ('POST','DELETE')),
 path text NOT NULL, body_hash text NOT NULL, created_at timestamptz NOT NULL,
 attempt_number integer NOT NULL CHECK(attempt_number=1), state text NOT NULL,
 may_have_been_sent boolean NOT NULL,
 CHECK(mode <> 'DEMO' OR host='https://external-api.demo.kalshi.co/trade-api/v2')
);
CREATE TABLE local_demo_orders(
 execution_id text PRIMARY KEY REFERENCES execution_journals(execution_id),
 client_order_id text NOT NULL UNIQUE, intent_hash text NOT NULL,
 mode execution_mode NOT NULL, ticker text NOT NULL, outcome_side text NOT NULL,
 price numeric NOT NULL CHECK(price>0 AND price<1),
 quantity numeric NOT NULL CHECK(quantity>0), filled_quantity numeric NOT NULL,
 fee numeric NOT NULL CHECK(fee>=0), state text NOT NULL,
 exchange_order_id text UNIQUE, subaccount integer NOT NULL CHECK(subaccount=0),
 reconciliation_required boolean NOT NULL, updated_at timestamptz NOT NULL,
 CHECK(filled_quantity>=0 AND filled_quantity<=quantity)
);
CREATE TABLE demo_order_state_events(
 event_key text PRIMARY KEY, execution_id text NOT NULL REFERENCES local_demo_orders(execution_id),
 sequence bigint, state text NOT NULL, happened_at timestamptz NOT NULL, source text NOT NULL
);
CREATE TABLE demo_exchange_fill_events(
 trade_id text PRIMARY KEY, execution_id text NOT NULL REFERENCES local_demo_orders(execution_id),
 order_id text NOT NULL, ticker text NOT NULL, price numeric NOT NULL,
 count_fp numeric NOT NULL CHECK(count_fp>0), fee_cost numeric NOT NULL CHECK(fee_cost>=0),
 is_taker boolean NOT NULL, outcome_side text NOT NULL, matching_time timestamptz NOT NULL,
 subaccount integer NOT NULL CHECK(subaccount=0)
);
CREATE TABLE demo_reconciliation_runs(
 run_id text PRIMARY KEY, execution_id text REFERENCES local_demo_orders(execution_id),
 started_at timestamptz NOT NULL, completed_at timestamptz,
 orders_match boolean, fills_match boolean, positions_match boolean,
 fees_match boolean, account_risk_match boolean, outcome text NOT NULL
);
CREATE TABLE demo_reconciliation_conflicts(
 conflict_id text PRIMARY KEY, run_id text NOT NULL REFERENCES demo_reconciliation_runs(run_id),
 kind text NOT NULL, detail_hash text NOT NULL, created_at timestamptz NOT NULL
);
CREATE TABLE demo_queue_observations(
 observation_hash text PRIMARY KEY, observed_at timestamptz NOT NULL,
 order_id text NOT NULL, market text NOT NULL, queue_position_fp numeric NOT NULL,
 remaining_quantity numeric NOT NULL, price numeric NOT NULL,
 quality text NOT NULL CHECK(quality='OBSERVED_DEMO_ORDER_QUEUE')
);
CREATE TABLE demo_fee_comparisons(
 comparison_id text PRIMARY KEY, predicted_fee numeric NOT NULL,
 actual_fee numeric NOT NULL, fee_policy_version text NOT NULL,
 created_at timestamptz NOT NULL
);
CREATE TABLE demo_slippage_comparisons(
 comparison_id text PRIMARY KEY, m10_slippage numeric NOT NULL,
 m11_slippage numeric NOT NULL, actual_demo_slippage numeric NOT NULL,
 created_at timestamptz NOT NULL
);
CREATE TABLE demo_execution_acceptance_runs(
 run_id text PRIMARY KEY, owner_actor text NOT NULL, started_at timestamptz NOT NULL,
 completed_at timestamptz, connectivity text NOT NULL, resting_lifecycle text NOT NULL,
 cancellation text NOT NULL, fill_lifecycle text NOT NULL,
 production_influence text NOT NULL CHECK(production_influence='NONE')
);
