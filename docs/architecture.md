# Architecture

## Boundary model

The system converts provider responses into one provider-neutral Platform Post
boundary. Fixed cross-adapter fields carry identity and ownership; an open
content object retains safe provider content without forcing every adapter into
one provider's schema.

```text
authorized public account locators + ordered source definitions
                │
                ▼
Settings → strict runtime parser → source composers → credential-bound instances
                │                                      │
                └──────── normal Account Posts Router ─┘
                              │ strict actor-URL ownership
                              ▼
                     Post Router + per-account windows
                              │
                   unified Platform Posts
                              │
                              ▼
                 deterministic merge → atomic snapshot tree
                              │
                              ▼
                 validate → immutable leased `dist` publication
```

The only currently supported production source ID is `brightdata`, which
explicitly composes the Bright Data LinkedIn adapter. No discovery mechanism,
module path, or environment-provided class name creates arbitrary adapters or
credential clients at runtime.

## Canonical records and schemas

The checked-in JSON Schemas are the public persistence contract:

- [`schemas/account.schema.json`](../schemas/account.schema.json) defines an
  Account with `platform`, `kind`, canonical `profile_url`, and `first_seen_at`.
  `profile_url` is the sole persisted identity; the runtime `id` property is an
  alias used by routing and merge code.
- [`schemas/post.schema.json`](../schemas/post.schema.json) defines a unified
  Platform Post with `platform_post_id`, `account_profile_url`, canonical URL,
  publication/first-seen timestamps, `type`, and `content`. The fixed boundary
  fields reject unknown keys. `content` intentionally accepts recursive JSON so
  adapters can retain text, media, documents, links, metrics, and future fields.

There is no persisted format version, provider source-record format, or
manifest format. The repository validates the exact current contract instead
of maintaining compatibility readers. The schema generator is `pixi run
schemas`; `pixi run schemas-check` proves the checked-in files match it.

## Account and post identity

`ACCOUNTS` is newline-delimited public LinkedIn person or company locators.
The parser canonicalizes accepted locators, de-duplicates them while preserving
first occurrence, and rejects malformed hosts, credentials-in-URLs, ports,
query-like path variations, control characters, invalid percent escaping, and
percent-encoded slug variants, and non-person/company paths. It does not retain
arbitrary user text downstream.

The strict locator parser is the only Account canonicalization authority. For
every persisted Account, `profile_url` is the parser's canonical person/company
URL. Runtime `Account.id` returns the same value. `Post.account_profile_url`
must equal that URL. Requests are routed directly
through the normal Posts route in kind-homogeneous batches (maximum 20); there
is no Identity/Profile lookup route.

A changed person or company slug canonicalizes to a new URL and is a distinct
Account. The old and new URL histories can coexist. Numeric provider identity,
alias reconciliation, migration, compatibility loading, and entity merging are
not supported in this repository. Downstream consumers own those
cross-URL decisions.

Every successful Bright Data record must contain at least one of
`use_url, user_url, profile_url, and company_url`. Ownership validation parses
every supplied actor URL with the strict locator parser, requires every URL to
have the requested Account kind, requires all of them to canonicalize to one
URL, and then requires that URL to equal the exact requested Account URL.
`user_id` is optional provider payload data only and cannot establish ownership
when actor URL evidence is absent.

Canonical runtime post identity is `linkedin:post:<platform-post-id>`. It is
derived for filenames and merge checks but is not duplicated in JSON. An
equivalent repeated record is deduplicated; conflicting account ownership or
content is a schema/integrity abort, not a merge preference.

## Adapter registry, metadata, and credential instances

An adapter driver declares immutable metadata at class definition time:
platform, supported operations, supported account kinds, and whether it batches.
`AdapterRegistry` is an explicit ordered tuple. It rejects missing/malformed
metadata, duplicate driver classes, and empty or repeated operations/account
kinds. Multiple drivers may declare the same capability; a lookup returns every
driver matching `(platform, operation, account kind)`. Registry order remains
deterministic capability metadata; runtime source order, not registry order,
defines provider priority.

`SOURCES` is a newline-delimited ordered list. Every non-empty line is parsed by
splitting only its first colon into `<source_id>:<api_token>`. Source IDs are
case-normalized and resolved through a code-owned explicit allowlist; malformed
or unsupported IDs reject the complete input before client creation. Exact
`source_id + token` duplicates are removed while preserving first occurrence.
The token may contain additional colons and remains secret material.

Each source composer returns only the adapter instances that provider explicitly
supports. Enabling a source never creates a Cartesian product with every known
platform. One source line may produce multiple instances only when its code-owned
composer explicitly declares those adapters. Instances receive one global
run-local ordinal in source and composer order. Credentials never become record
fields, output values, diagnostic strings, or persistent fingerprints.
`build_runtime`, `compose_runtime`, and the collection runtime-builder hook are
the extension points for additional providers.

The Router owns exactly one immutable heterogeneous instance tuple shared by
every Posts batch. Every eligible batch starts with the first compatible source
in `SOURCES` order. A retryable failure may move to the next healthy source;
quota exhaustion or invalid credentials disable only that instance for this
run. Invalid/not-found accounts are
account-scoped and do not rotate credentials. A schema or ownership corruption
aborts the run and suppresses candidate posts. If any instantiated fallback for
a capability cannot batch, the Router uses single-account batches so every
fallback remains valid.

## Per-account collection windows

Each accepted `AdapterPostRequest` has one canonical account and one inclusive
UTC date range. Windows are calculated independently, not as a global provider
filter:

- An account absent from the previous snapshot's Account set starts at the
  LinkedIn launch date, `2003-05-05`, and ends at `run_start_date`.
- An account already present in the previous snapshot starts at
  `run_start_date - 3 days` and ends at `run_start_date`. Because the range is
  inclusive, this covers the run date and the preceding three UTC dates.
- Account presence, not Post presence, determines the default. An Account that
  previously produced zero Posts still uses the three-day window on its next
  run.
- A complete explicit `--start-date` and `--end-date` pair replaces both
  defaults for every account. One missing side or an inverted range is rejected
  before provider I/O.

The overlap protects against late publication and provider availability.
Identical duplicate account/window requests collapse; different windows for
the same account fail before collection. An Adapter must return the complete
available result for its requested window or classify the attempt as a failure.
The Bright Data client accepts a snapshot only after it reaches `ready`, then
downloads `format=json` and requires a valid complete JSON list with no
embedded provider errors. It explicitly requests up to 1,000 results per
Account; person requests also set `only_authored_posts=true`. Bright Data
exposes no independently verifiable truncation marker, so an Account with more
than 1,000 posts in one requested window requires narrower backfill windows
before the application can claim complete history.

## Snapshot storage and atomic candidate creation

Snapshots have one root and the exact layout below. Paths are stable and all
records are deterministic JSON.

```text
<snapshot-root>/
├── accounts/<encoded canonical Account ID>.json
├── accounts.json                         # profile_url → record path map
└── posts/linkedin/<encoded runtime Post ID>.json
```

There are no derived feed, source-copy, or manifest files. Verification reloads
the complete tree, regenerates the expected account map and record paths, and
rejects inventory, index, schema, ownership, or byte inconsistencies. Counts and
a SHA-256 digest of the complete tree are calculated in memory for CLI and CI
comparison, but are not persisted into the collected dataset.

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

A successful response with zero records creates the requested URL Account.
Reply, repost, quote, media-only, and unknown provider post types are emitted as
Platform Posts rather than discarded. A typed `NOT_FOUND` is an account-scoped failure instead: it
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
