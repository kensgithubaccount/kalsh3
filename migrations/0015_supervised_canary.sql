-- M16 supervised one-contract workflow. No credential or signature material.
CREATE TABLE canary_readiness_snapshots(
 snapshot_id text PRIMARY KEY, observed_at timestamptz NOT NULL,
 missing_gates text[] NOT NULL, live_demo_verified boolean NOT NULL,
 production_read_verified boolean NOT NULL, reconciliation_verified boolean NOT NULL,
 api_compatibility_verified boolean NOT NULL, postgres_verified boolean NOT NULL,
 signer_runtime_verified boolean NOT NULL, content_hash text NOT NULL UNIQUE);
CREATE TABLE canary_previews(
 preview_id text PRIMARY KEY, candidate_id text NOT NULL, content_hash text NOT NULL UNIQUE,
 client_order_id text NOT NULL UNIQUE, price numeric NOT NULL, quantity numeric NOT NULL CHECK(quantity=1.00),
 max_fee numeric NOT NULL, max_loss numeric NOT NULL, rules_hash text NOT NULL,
 reconciliation_version text NOT NULL, created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL CHECK(expires_at<=created_at+interval '2 minutes'));
CREATE TABLE human_canary_approvals(
 approval_id text PRIMARY KEY, preview_hash text NOT NULL UNIQUE,
 owner_identity text NOT NULL, step_up_proof_reference text NOT NULL,
 content_hash text NOT NULL UNIQUE, approved_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL CHECK(expires_at<=approved_at+interval '60 seconds'),
 state text NOT NULL CHECK(state IN ('ISSUED','CONSUMED','EXPIRED','REVOKED')));
CREATE TABLE canary_sessions(
 session_id text PRIMARY KEY, preview_id text NOT NULL UNIQUE,
 approval_id text NOT NULL UNIQUE, client_order_id text NOT NULL UNIQUE,
 state text NOT NULL, filled_quantity numeric NOT NULL DEFAULT 0,
 remaining_quantity numeric NOT NULL DEFAULT 1.00,
 possibly_submitted boolean NOT NULL DEFAULT false, reconciliation_version text,
 created_at timestamptz NOT NULL, resolved_at timestamptz,
 CHECK(filled_quantity>=0 AND remaining_quantity>=0 AND filled_quantity+remaining_quantity=1.00));
CREATE UNIQUE INDEX one_unresolved_real_canary ON canary_sessions((true)) WHERE state IN
 ('READY_FOR_APPROVAL','AWAITING_REAUTH','HUMAN_APPROVED','FINAL_REVALIDATION',
  'CANARY_AUTHORIZED','SUBMISSION_PENDING','SUBMITTED_OR_UNKNOWN','RECONCILING');
CREATE TABLE production_fill_counter(
 singleton smallint PRIMARY KEY CHECK(singleton=1), real_fill_count integer NOT NULL CHECK(real_fill_count BETWEEN 0 AND 50));
INSERT INTO production_fill_counter VALUES(1,0);
CREATE TABLE canary_state_events(
 event_id bigserial PRIMARY KEY, happened_at timestamptz NOT NULL,
 event_type text NOT NULL, reference_hash text NOT NULL, actor text NOT NULL);
