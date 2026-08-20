# Architecture

## Boundary model

The system separates provider-specific evidence from canonical subscription
data. This is intentional: a provider payload can change as metrics or fields
evolve without silently changing the canonical account/post model.

```text
authorized public account locators + credential pool
                │
                ▼
Settings → strict input parser → explicit registry → credential-bound instances
                │                                      │
                └──────── Identity Router ─────────────┘
                              │ canonical Account identities
                              ▼
                     Post Router + per-account windows
                              │
          provider source records + canonical original Posts
                              │
                              ▼
                 deterministic merge → atomic snapshot tree
                              │
                              ▼
                 validate → immutable leased `dist` publication
```

The only currently registered production driver is the Bright Data LinkedIn
adapter. Its presence is explicit in bootstrap; no discovery mechanism creates
arbitrary adapters or credential clients at runtime.

## Canonical records and schemas

The checked-in JSON Schemas are the public persistence contract:

- [`schemas/account.schema.json`](../schemas/account.schema.json) defines a
  canonical `Account`: `schema_version`, canonical `id`, `platform`, `kind`,
  numeric `platform_account_id`, stable `profile_url`, sorted unique
  `url_aliases`, and `first_seen_at`.
- [`schemas/post.schema.json`](../schemas/post.schema.json) defines a canonical
  original `Post`: `schema_version`, canonical `id`, provider post ID, owning
  `account_id`, canonical URL, published/first-seen timestamps, text, fixed
  `kind: "original"`, de-duplicated hashtags/links, and content hash.
- [`schemas/brightdata-linkedin-post.schema.json`](../schemas/brightdata-linkedin-post.schema.json)
  defines the provenance record: fixed `provider: "brightdata"` and dataset,
  provider post ID, canonical account ownership, payload SHA-256, and the
  provider payload. It is evidence, not the downstream canonical Post.

All three formats currently have `schema_version: 1` and forbid unknown fields.
The schema generator is `pixi run schemas`; `pixi run schemas-check` proves the
checked-in files match the generator.

## Account and post identity

`ACCOUNTS` is newline-delimited public LinkedIn person or company locators.
The parser canonicalizes accepted locators, de-duplicates them while preserving
first occurrence, and rejects malformed hosts, credentials-in-URLs, ports,
query-like path variations, control characters, invalid percent escaping, and
non-person/company paths. It does not retain arbitrary user text downstream.

Identity routing starts with canonical local `Account` records from the prior
snapshot. Known aliases resolve locally. Unknown locators are routed in
kind-homogeneous batches (maximum 20) through the same instance pool that later
collects posts. Account identity results become canonical account IDs; post
routing validates that every source record and canonical Post belongs to that
account before it is accepted.

Canonical post identity is `linkedin:post:<provider-post-id>`. The provider
source record is keyed by the same canonical post identity and includes the raw
payload's hash. An equivalent repeated record is deduplicated; conflicting
account ownership or payload hash is a schema/integrity abort, not a merge
preference.

## Adapter registry, metadata, and credential instances

An adapter driver declares immutable metadata at class definition time:
platform, supported operations, supported account kinds, and whether it batches.
`AdapterRegistry` is an explicit ordered tuple. It rejects missing/malformed
metadata, duplicate driver classes, duplicate capability descriptors, empty or
repeated operations/account kinds. A capability lookup returns only drivers that
match `(platform, operation, account kind)` in declared order.

`BRIGHT_DATA_API_KEYS` is parsed as newline-delimited secret material, empty
lines removed, and duplicate strings removed while preserving order. Bootstrap
creates exactly one opaque `AdapterInstance` for each parsed credential and
assigns a run-local ordinal. Credentials never become record fields, output
values, diagnostic strings, or persistent fingerprints.

The Router owns that one immutable instance tuple. Both identity and post routes
reuse it, so identity resolution cannot build a second pool. For an eligible
batch, instances are deterministically rotated. A retryable failure may move to
the next healthy instance; quota exhaustion or invalid credentials disable only
that instance for this run. Invalid/not-found accounts are account-scoped and do
not rotate credentials. A schema/identity corruption aborts the run and
suppresses candidate posts/source records.

## Per-account collection windows

Each accepted `AdapterPostRequest` has one canonical account and one inclusive
UTC date range. Windows are calculated independently, not as a global provider
filter:

- A previously unseen account starts at `run_start_date - 7 days` and ends at
  `run_start_date`.
- A known account starts at that account's newest persisted `published_at` date
  minus 3 days and ends at `run_start_date`.
- A complete explicit `--start-date` and `--end-date` pair replaces both
  defaults for every account. One missing side or an inverted range is rejected
  before provider I/O.

The overlap protects against late availability and changes around the last
observed boundary. Identical duplicate account/window requests collapse;
different windows for the same account fail before collection.

## Snapshot storage and atomic candidate creation

Snapshots have one root and the exact layout below. Paths are stable and all
records are deterministic JSON.

```text
<snapshot-root>/
├── accounts/<encoded canonical Account ID>.json
├── accounts.json                         # Account ID → record path index
├── feed.json                             # canonical Post IDs in feed order
├── posts/linkedin/<encoded canonical Post ID>.json
├── source/brightdata/linkedin/posts/<encoded canonical Post ID>.json
└── snapshot.json                         # counts + digest, written last
```

`snapshot.json` contains `schema_version`, `account_count`, `post_count`,
`source_record_count`, and a 64-character SHA-256 digest. The digest is computed
over every non-manifest file in POSIX path order as `path UTF-8`, NUL byte, then
the exact bytes. Verification reloads the complete tree and rejects inventory,
index, schema, ownership, ordering, or digest inconsistencies.

A candidate is never assembled in the destination root. The repository writes,
reloads, and validates a sibling temporary tree, then promotes it atomically.
Interrupted serialization/copy recovery removes its partial sibling and keeps
the previous canonical root byte-identical. Merge preserves historical records
for failed or zero-result accounts; only successful routes contribute current
candidate state. A byte-identical candidate is explicitly `unchanged`.

## Immutable `dist` publication lease

Publication is deliberately more restrictive than normal source history:

1. The workflow observes `refs/heads/dist` once and records its exact SHA (or
   `absent`).
2. It materializes and verifies that exact prior snapshot in an isolated
   temporary repository, then collects and validates the candidate.
3. The publisher re-observes the advertised ref immediately before publication.
   Any mismatch is a stale lease failure (`6`).
4. A changed candidate is built as a new Git tree and root commit with no
   parent, then pushed only with the matching `--force-with-lease`. An unchanged
   candidate still performs the lease check and reports `unchanged`.

There is no retry on a stale lease, no plain `--force`, and no fallback that
changes source checkout history. The branch's destructive replacement is safe
only because the lease names the exact observed snapshot and each branch tip is
a complete, independently verifiable snapshot.
