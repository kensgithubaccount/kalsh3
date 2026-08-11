-- M17 bounded-autonomy research governance. Activation is structurally absent.
CREATE TABLE autonomy_runtime_state(
 singleton smallint PRIMARY KEY CHECK(singleton=1),
 autonomy_state text NOT NULL CHECK(autonomy_state='OFF'),
 production_state text NOT NULL CHECK(production_state='DISARMED'),
 production_write_credential text NOT NULL CHECK(production_write_credential='NONE'));
INSERT INTO autonomy_runtime_state VALUES(1,'OFF','DISARMED','NONE');
CREATE TABLE autonomy_readiness_snapshots(
 snapshot_id text PRIMARY KEY, observed_at timestamptz NOT NULL,
 policy_version text NOT NULL, code_sha text NOT NULL,
 missing_gates text[] NOT NULL, content_hash text NOT NULL UNIQUE,
 autonomy_state text NOT NULL CHECK(autonomy_state='OFF'));
CREATE TABLE bounded_autonomy_proposals(
 proposal_id text PRIMARY KEY, readiness_hash text NOT NULL,
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 rationale text NOT NULL, content_hash text NOT NULL UNIQUE,
 requested_state text NOT NULL CHECK(requested_state='OFF'),
 production_influence text NOT NULL CHECK(production_influence='NONE'));
CREATE TABLE autonomy_governance_events(
 event_id bigserial PRIMARY KEY, happened_at timestamptz NOT NULL,
 event_type text NOT NULL, reference_hash text NOT NULL);
