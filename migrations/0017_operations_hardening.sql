-- M18 immutable operational evidence. No table can arm or authorize production.
CREATE TABLE operational_snapshots(
 snapshot_id text PRIMARY KEY, observed_at timestamptz NOT NULL,
 readiness_state text NOT NULL CHECK(readiness_state IN ('READY_READ_ONLY','DEGRADED_READ_ONLY','NOT_READY')),
 blockers text[] NOT NULL, content_hash text NOT NULL UNIQUE,
 production_state text NOT NULL CHECK(production_state='DISARMED'),
 autonomy_state text NOT NULL CHECK(autonomy_state='OFF'));
CREATE TABLE backup_manifests(
 backup_id text PRIMARY KEY, created_at timestamptz NOT NULL,
 schema_version text NOT NULL, encrypted boolean NOT NULL CHECK(encrypted),
 content_hash text NOT NULL UNIQUE);
CREATE TABLE restore_drills(
 drill_id text PRIMARY KEY, backup_id text NOT NULL REFERENCES backup_manifests(backup_id),
 performed_at timestamptz NOT NULL, isolated_target boolean NOT NULL CHECK(isolated_target),
 checksums_verified boolean NOT NULL, migrations_verified boolean NOT NULL,
 row_counts_verified boolean NOT NULL, application_smoke_verified boolean NOT NULL,
 production_network_blocked boolean NOT NULL CHECK(production_network_blocked),
 passed boolean NOT NULL);
CREATE TABLE operational_incidents(
 incident_id text PRIMARY KEY, opened_at timestamptz NOT NULL,
 severity text NOT NULL CHECK(severity IN ('INFO','WARNING','CRITICAL')),
 safe_code text NOT NULL, status text NOT NULL CHECK(status IN ('OPEN','MITIGATED','CLOSED')),
 production_state text NOT NULL CHECK(production_state='DISARMED'));
