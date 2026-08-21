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
                └──────── normal Account Posts Router ─┘
                              │ strict actor-URL ownership
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
  canonical `Account`: `schema_version: 2`, canonical URL `id`, `platform`,
  `kind`, the identical canonical URL `profile_url`, and `first_seen_at`.
- [`schemas/post.schema.json`](../schemas/post.schema.json) defines a canonical
  original `Post`: `schema_version: 2`, canonical `id`, provider post ID,
  canonical URL owning `account_id`, canonical Post URL,
  published/first-seen timestamps, text, fixed `kind: "original"`,
  de-duplicated hashtags/links, and content hash.
- [`schemas/brightdata-linkedin-post.schema.json`](../schemas/brightdata-linkedin-post.schema.json)
  defines the `schema_version: 2` provenance record: fixed
  `provider: "brightdata"` and dataset, provider post ID, canonical URL
  account ownership, payload SHA-256, and the provider payload. It is evidence,
  not the downstream canonical Post.

All three record formats forbid unknown boundary fields. Legacy Account, Post,
and source-record v1 records and snapshots are rejected; there is no dual-read
or conversion path. The snapshot manifest retains its existing shape and
`schema_version: 1`, and its digest algorithm is unchanged. The schema generator
is `pixi run schemas`; `pixi run schemas-check` proves the checked-in files match
the generator.

## Account and post identity

`ACCOUNTS` is newline-delimited public LinkedIn person or company locators.
The parser canonicalizes accepted locators, de-duplicates them while preserving
first occurrence, and rejects malformed hosts, credentials-in-URLs, ports,
query-like path variations, control characters, invalid percent escaping, and
non-person/company paths. It does not retain arbitrary user text downstream.

The strict locator parser is the only Account canonicalization authority. For
every persisted Account, `Account.id == Account.profile_url` and both values are
the parser's canonical person/company URL. `Post.account_id` and the Bright Data
source-record account_id must equal that same URL. Requests are routed directly
through the normal Posts route in kind-homogeneous batches (maximum 20); there
is no Identity/Profile lookup route.

A changed person or company slug canonicalizes to a new URL and is a distinct
Account. The old and new URL histories can coexist. Numeric provider identity,
alias reconciliation, migration, compatibility loading, and entity merging are
not supported in this repository.

Every successful Bright Data record must contain at least one of
`use_url, user_url, profile_url, and company_url`. Ownership validation parses
every supplied actor URL with the strict locator parser, requires every URL to
have the requested Account kind, requires all of them to canonicalize to one
URL, and then requires that URL to equal the exact requested Account URL.
`user_id` is optional provider payload data only and cannot establish ownership
when actor URL evidence is absent.

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

The Router owns exactly one immutable instance tuple shared by every Posts
batch. There is no identity resolution path and no second credential pool. For
an eligible batch, instances are deterministically rotated. A retryable failure
may move to the next healthy instance; quota exhaustion or invalid credentials
disable only that instance for this run. Invalid/not-found accounts are
account-scoped and do not rotate credentials. A schema or ownership corruption
aborts the run and suppresses candidate posts/source records.

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
reloads, and validates a private sibling temporary tree, then promotes it
atomically. Parent, root, candidate, directory, and file access is anchored to
no-follow file descriptors and verified by device/inode identity. The final
parent must be owned by the running user and must not be group- or
world-writable; concurrent non-cooperating processes with the same effective
user ID are outside the filesystem threat model. A replaced path, symlink,
special file, or identity mismatch fails closed without reading, overwriting,
or deleting the replacement. Interrupted serialization or promotion removes a
verified partial sibling and keeps the previous canonical root byte-identical.
Merge preserves historical records for failed or zero-result accounts; only
successful routes contribute current candidate state. A byte-identical
candidate is explicitly `unchanged`.

The write boundary may replace an existing, descriptor-verified empty output
directory created by the workflow as a candidate-path placeholder. That
exception does not apply to snapshot reads: an empty snapshot root remains an
inventory integrity failure.

A successful response with zero records, or with only non-original records,
creates the requested URL Account while emitting zero canonical Posts and
source records. A typed `NOT_FOUND` is an account-scoped failure instead: it
does not create a newly requested Account, and a failed refresh preserves prior
history. Ownership, schema, batch-coverage, duplicate-payload, and referential
conflicts abort the whole candidate before promotion. The candidate and its
counters are suppressed, and the prior snapshot remains byte-identical.

For typed input/`NOT_FOUND` and terminal provider failures, the CLI field
`failed_account_ids` contains canonical requested LinkedIn URLs. Account URLs
are the intentionally observable identifiers in that field; credentials,
provider bodies, snapshot IDs, and exception internals remain protected.

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
