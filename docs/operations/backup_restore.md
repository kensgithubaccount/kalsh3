# Backup and restore

## Policy

PostgreSQL is the financial system of record. Redis is a disposable cache; NATS JetStream and object
archives are reconstructed or restored from their separately versioned storage. Backups are encrypted
before leaving the host, content-addressed, retained outside the Oracle boot volume, and never contain
decrypted production-write key material. The target is daily database backup, seven daily and four weekly
copies, and a quarterly isolated restore drill. Production activation remains independent of restoration.

## Backup

1. Provision an `age` recipient as `/run/secrets/backup_age_recipient`; keep its identity offline.
2. Run `deploy/oracle/backup.sh` from a restricted service/timer identity.
3. Copy the `.age` object and checksum to versioned object storage with retention/immutability enabled.
4. Record size, SHA-256, schema version, upload result, and age as safe metrics. Do not record database
   credentials, account identifiers, dump contents, or encryption identities.
5. Alert if the last verified encrypted backup is older than 26 hours or the object upload is unknown.

## Restore drill

`deploy/oracle/restore-drill.sh BACKUP.age` verifies the checksum, decrypts into a mode-0700 temporary
directory, starts a disposable PostgreSQL 16 container with `--network none`, restores with
`--exit-on-error`, verifies that application tables exist, and destroys the container and plaintext.

A real drill also records migration compatibility, representative row counts, journal/reconciliation
invariants, application read-only smoke tests, and recovery time. It must never point at production,
inherit production networking, arm production, install a write credential, or retry an unknown mutation.

Docker/object-storage runtime and a real restore drill are **NOT VERIFIED** in the offline development
environment until independently executed and recorded.
