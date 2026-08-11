-- M15 production execution architecture. Credential/signature columns intentionally do not exist.
CREATE TYPE production_execution_state AS ENUM(
 'DISARMED','CANARY_PENDING_APPROVAL','SUPERVISED_CANARY','ARMED_BOUNDED');
CREATE TABLE production_execution_state_current(
 singleton smallint PRIMARY KEY CHECK(singleton=1),
 state production_execution_state NOT NULL CHECK(state='DISARMED'),
 restarted_at timestamptz NOT NULL DEFAULT now());
INSERT INTO production_execution_state_current VALUES(1,'DISARMED',now());
CREATE TABLE production_submission_journals(
 execution_id text PRIMARY KEY, authorization_id text NOT NULL UNIQUE,
 decision_id text NOT NULL, intent_hash text NOT NULL,
 client_order_id text NOT NULL UNIQUE,
 operation text NOT NULL CHECK(operation IN ('CREATE','CANCEL','AMEND','DECREASE')),
 authority_class text NOT NULL CHECK(authority_class IN ('NEW_RISK','RISK_REDUCING','EMERGENCY_SAFETY')),
 origin text NOT NULL CHECK(origin='https://external-api.kalshi.com'),
 method text NOT NULL CHECK(method IN ('POST','DELETE')),
 path text NOT NULL, body_hash text NOT NULL, envelope_hash text NOT NULL UNIQUE,
 boundary_version text NOT NULL, created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, state text NOT NULL,
 possibly_sent boolean NOT NULL, reconciliation_required boolean NOT NULL,
 CHECK(expires_at > created_at AND expires_at <= created_at + interval '5 seconds'));
CREATE TABLE production_signer_request_claims(
 authorization_id text PRIMARY KEY, execution_id text NOT NULL UNIQUE,
 envelope_hash text NOT NULL UNIQUE, claimed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE production_safe_events(
 event_id bigserial PRIMARY KEY, happened_at timestamptz NOT NULL,
 event_type text NOT NULL, execution_id text, reason_code text NOT NULL,
 CHECK(event_type IN (
  'PRODUCTION_SIGNER_STARTED','PRODUCTION_SIGNER_DISARMED',
  'WRITE_CREDENTIAL_ENROLLMENT_ATTEMPTED','WRITE_CREDENTIAL_VALIDATED',
  'WRITE_CREDENTIAL_REMOVED','SIGN_REQUEST_RECEIVED','SIGN_REQUEST_REJECTED',
  'PRODUCTION_REQUEST_PREPARED','PRODUCTION_SEND_BLOCKED_DISARMED',
  'PRODUCTION_AUTHORIZATION_EXPIRED','PRODUCTION_RECONCILIATION_REQUIRED'));
