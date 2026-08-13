# Future work

CryoPit’s first-generation multi-user design is intended to run behind an
institution’s existing single sign-on system. An authenticating reverse proxy
supplies a stable institutional user identifier; CryoPit uses that identifier
as the owner of each pit and applies it to every list, load, edit, attachment,
download, and recovery query. The current application therefore provides a
simple, defensible rule: authenticated users work with their own records.

A standalone field laptop remains a supported deployment. With proxy
authentication disabled, every browser using that CryoPit instance shares the
configured local identity. That is deliberate for one-laptop field work and is
not a substitute for SSO on a shared institutional service.

## Role-based and collaborative access

Roles are not required for the first SSO-backed release. Supervisors who need
whole-dataset reporting can query a read-only database backup outside CryoPit.
A future release may expose cross-owner access through CryoPit’s UI and API.
That work would add authorization on top of SSO authentication.

The preferred direction is just-in-time identity provisioning: CryoPit does not
need a directory of every institutional user in advance. A local user record is
created when a person first signs in, keyed by the stable identifier supplied by
SSO. Application-specific assignments can then be attached to that identity.
An administrator who must assign access before first login would need the
person’s stable institutional identifier.

Role information could come from:

- institutional SSO group claims for broad roles;
- CryoPit-managed assignments for application-specific access;
- a hybrid model, with SSO groups defining broad roles and CryoPit defining
  campaign scope.

A minimal permission model would distinguish:

- users who create, read, and update their own pits;
- supervisors who read assigned campaign records, read-only by default;
- administrators who manage recovery, access assignments, and ownership
  transfers.

Implementation should use explicit permissions rather than scattered role-name
checks, for example `pit:read-own`, `pit:update-own`, `pit:read-assigned`,
`pit:update-assigned`, `pit:read-all`, `recovery:manage`, and `roles:manage`.

CryoPit already stores campaigns as stable database records and pits reference an immutable `campaign_id`. Campaign-scoped authorization would add memberships linking institutional identities to those records. Because authors can currently get-or-create campaigns by entering a code, future authorization must validate membership server-side rather than treating arbitrary campaign text as an access grant.

The complete collaboration design should also include:

- ownership transfer when staff leave or accounts change;
- read-only and edit-capable campaign memberships;
- audit logging for cross-owner reads and changes;
- a documented mapping from institutional group claims to CryoPit permissions;
- administrative recovery that never bypasses attachment ownership checks;
- migration handling if an institution changes the identifier emitted by SSO.

## Other planned work

### Institutional deployment validation

Stage 12 provides the application-side SSO boundary, CSRF controls, security headers, health checks, release configuration, consistent backup/restore tooling, and route-level owner-isolation tests. The institution must still validate its specific SSO gateway, header mapping, TLS policy, logging, monitoring, restore drill, concurrent workload, large archives, large photo queues, and browser quota behavior before production approval. A supported read-only reporting path for supervisors should operate on verified backup copies rather than the live database.

### Transfer workflow enhancements

Stage 14 delivers checksum-verified, one-way field-to-central transfer bundles,
append-only revision ancestry, dry-run classification, server-controlled owner
mapping, idempotent import, fast-forward-only updates, attachment transfer,
conflict quarantine, and an import audit trail. See [MERGING.md](MERGING.md).

Possible later enhancements include cryptographic bundle signatures, an
administrative upload/review interface, explicit conflict-resolution tooling,
batch owner-mapping policies, transfer-retention policy, and a separately
designed deletion protocol. CryoPit should not evolve this into implicit
two-way synchronization without a new conflict and authorization design.

### Browser policy and CSP tightening

Move inline event handlers and styles into static assets so the Content Security Policy can remove `'unsafe-inline'`. Continue cross-browser field testing for IndexedDB persistence, HEIC handling, storage quotas, refresh/restart recovery, and managed-browser policies.
