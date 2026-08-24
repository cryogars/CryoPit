# Upgrade and rollback guide

CryoPit migrations run at application startup and are designed to preserve the
existing database transactionally. Filesystem changes remain recoverable through
pending archive and attachment journals.

## Before upgrading

1. read `CHANGELOG.md` and the release report;
2. stop writes or remove the instance from the proxy pool;
3. create and verify a CryoPit backup bundle;
4. record the current application ZIP/image digest and environment file;
5. copy the release candidate to a new application directory;
6. confirm Python 3.11+ and install `requirements.lock` in a new virtual or Conda environment;
7. run the complete test suite against disposable data.

## Upgrade

Start one instance against a copy of production data first. Confirm database
migration, `/readyz`, owner-scoped workspace results, archive/re-archive,
attachment download, and recovery lists. Then schedule the production restart
and keep the previous application directory intact.

## Rollback

Application-only rollback is safe only when the prior release understands the
post-upgrade schema. Otherwise:

1. stop the new application;
2. restore the verified pre-upgrade bundle with `python -m cryopit.ops restore`;
3. restore the previous application build and environment;
4. start it privately and verify health, readiness, pits, and attachments;
5. return it to the proxy pool.

Never point two different CryoPit versions at the same live SQLite database and
export tree simultaneously.

## Stage 14 revision migration

On first Stage 14 startup, CryoPit creates `app_metadata`, `site_revisions`,
`transfer_imports`, and `transfer_import_items`, adds
`sites.current_revision_id`, generates one persistent installation UUID, and
backfills each readable pre-existing pit as revision 1 without changing its
scientific payload or export folder.

A pre-Stage-14 application does not understand imported revision/audit state.
Use the verified pre-upgrade backup for rollback rather than continuing to write
with an older build against the migrated live database.
