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

The supported production source IDs are `apify` and `brightdata`. Apify
explicitly composes LinkedIn and X adapters; Bright Data explicitly composes its
LinkedIn adapter. No discovery mechanism, module path, or environment-provided
class name creates arbitrary adapters or credential clients at runtime. The X
adapter uses the approved Xquik Actor contract. It does not impose a run-level
USD charge limit; repository capability does not by itself authorize a live
collection.

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
- [`schemas/posts-index.schema.json`](../schemas/posts-index.schema.json) defines
  the derived `posts.json` locator index. Each entry contains only `path`,
  `account_profile_url`, `published_at`, and `platform`.

The Account and Post schemas use platform-specific `oneOf` branches so an X
owner cannot be paired with a LinkedIn platform or canonical Post URL, and vice
versa. Runtime validation remains authoritative for semantic relationships JSON
Schema cannot express, including equality between an X status ID and
`platform_post_id`.

There is no persisted format version, provider source-record format, or
snapshot manifest. The repository validates the exact current contract instead
of maintaining compatibility readers. The schema generator is `pixi run
schemas`; `pixi run schemas-check` proves the checked-in files match it.

## Account and post identity

`ACCOUNTS` is a comma/newline-delimited list of public account locators. The parser accepts
LinkedIn person/company URLs and X profile URLs, canonicalizes them,
de-duplicates them while preserving first occurrence, and rejects malformed
hosts, credentials-in-URLs, ports, query-like path variations, control
characters, invalid percent escaping, percent-encoded slug variants, and
unsupported paths. It does not retain arbitrary user text downstream. Both
LinkedIn and X locators have explicit production source composition.

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
`use_url, user_url, profile_url, and company_url`. Bright Data ownership
validation parses every supplied actor URL with the strict locator parser,
requires every URL to have the requested Account kind, requires all of them to
canonicalize to one URL, and then requires that URL to equal the exact requested
Account URL. `user_id` is optional provider payload data only and cannot
establish ownership when actor URL evidence is absent. The Apify LinkedIn
adapter uses the Actor record's `query.targetUrl`, applies the same strict
Account canonicalization, and requires an exact match to the requested Account
URL. This request identity is retained only long enough to validate ownership
and is not persisted. `author` remains LinkedIn post content because reposts can
name the original author instead of the subscribed Account. The Apify X adapter
instead validates `author.username`, the numeric status ID, and the canonical
`x.com/<handle>/status/<id>` URL against the requested X profile.

Canonical runtime post identity is platform-qualified and is not duplicated in
JSON. LinkedIn uses `linkedin:post:<platform-post-id>`; X uses
`x:post:<numeric-status-id>`. X canonical Post URLs have the form
`https://x.com/<lowercase-handle>/status/<numeric-status-id>`, and runtime
validation requires the URL status ID to equal `platform_post_id`. The
LinkedIn identity is derived for filenames and merge checks. The
LinkedIn `activity` URN prefix is removed so the numeric Apify ID and the
equivalent Bright Data activity URN resolve to the same Platform Post. Other URN
namespaces remain distinct. Numeric activity IDs also produce one canonical
`https://www.linkedin.com/feed/update/urn:li:activity:<id>/` URL independent of
the provider's `/posts/` route. Publication timestamps are normalized to UTC
whole-second precision. A generic provider `post` type is corrected to `repost`
when the payload contains positive repost evidence; explicit `quote`, `reply`,
and future types remain unchanged. An equivalent repeated record is
deduplicated; conflicting account ownership or content is a schema/integrity
abort, not a merge preference.

LinkedIn `content` is a sparse canonical union, not a provider response schema.
Both adapters write `text`; media is represented as `images` and `videos` lists
whose string URLs become `{ "url": ... }` objects while safe provider-supplied
media attributes remain available. Common author, engagement, document, links,
and repost data use those same keys. Source names such as Bright Data
`num_likes`, `num_comments`, and document fields or Apify `postImages`,
`postVideo`, and document page count are moved into their canonical containers
instead of being duplicated. Safe unknown fields remain open for lossless
forward compatibility.

## Adapter registry, metadata, and credential instances

An adapter driver declares immutable metadata at class definition time:
platform, supported operations, supported account kinds, and whether it batches.
`AdapterRegistry` is an explicit ordered tuple. It rejects missing/malformed
metadata, duplicate driver classes, and empty or repeated operations/account
kinds. Multiple drivers may declare the same capability; a lookup returns every
driver matching `(platform, operation, account kind)`. Registry order remains
deterministic capability metadata. Production LinkedIn composition always puts
Apify instances before Bright Data instances. Production X composition
registers one Xquik adapter per Apify credential; Bright Data instances remain
ineligible for X routing.

`SOURCES` is a comma/newline-delimited ordered list. Every non-empty entry is parsed by
splitting only its first colon into `<source_id>:<api_token>`. Source IDs are
case-normalized and resolved through a code-owned explicit allowlist; malformed
or unsupported IDs reject the complete input before client creation. Exact
`source_id + token` duplicates are removed while preserving first occurrence.
The token may contain additional colons and remains secret material. Repeating
`apify` or `brightdata` with a different token creates another independent
credential-bound instance. Line order is preserved among credentials for the
same provider.

Each source composer returns only the adapter instances that provider explicitly
supports. Enabling a source never creates a Cartesian product with every known
platform. One source line may produce multiple instances only when its code-owned
composer explicitly declares those adapters. Instances receive one global
run-local ordinal in source and composer order. Credentials never become record
fields, output values, diagnostic strings, or persistent fingerprints.
`build_runtime`, `compose_runtime`, and the collection runtime-builder hook are
the extension points for additional providers.

The Router owns exactly one immutable heterogeneous instance tuple shared by
every Posts batch. Every eligible LinkedIn batch tries configured Apify
instances first, followed by configured Bright Data instances. A retryable
failure may move to the next healthy source; quota exhaustion or invalid
credentials disable only that instance for this run. Invalid/not-found accounts are
account-scoped and do not rotate credentials. A schema or ownership corruption
aborts the run and suppresses candidate posts. If any instantiated fallback for
a capability cannot batch, the Router uses single-account batches so every
fallback remains valid. Apify does not batch accounts: each Account gets one
Actor run with its own collection window. Once Apify may have accepted a paid
Actor run, a polling, dataset, timeout, or response-schema failure is
account-scoped and never rotates to another source in the same attempt. This
prevents duplicate paid runs after ambiguous remote acceptance.

## Per-account collection windows

Each accepted `AdapterPostRequest` has one canonical account and one inclusive
UTC date range. Windows are calculated independently, not as a global provider
filter:

- An account absent from the previous snapshot's Account set starts at its
  platform boundary and ends at `run_start_date`: LinkedIn uses `2003-05-05` and
  X uses `2006-03-21`.
- An account with a successful timestamp in `state.json` starts three days
  before that timestamp and ends at `run_start_date`, catching up after downtime.
  Legacy snapshots without that timestamp use `run_start_date - 3 days`.
- Account presence, not Post presence, determines the default. An Account that
  previously produced zero Posts still uses the three-day window on its next
  run.
- A complete explicit `--start-date` and `--end-date` pair replaces both
  defaults for every account. One missing side or an inverted range is rejected
  before provider I/O.

The overlap protects against late publication and provider availability.
Identical duplicate account/window requests collapse; different windows for
the same account fail before collection. An Adapter must return the complete
provider response for its requested window or classify the attempt as a failure.
The Bright Data client accepts a snapshot only after it reaches `ready`, then
downloads `format=json` and requires a valid complete JSON list with no
embedded provider errors. It explicitly requests up to 1,000 results per
Account; person requests also set `only_authored_posts=true`. Bright Data
documents profile discovery as feed monitoring and does not provide a cursor,
offset, page token, or other traversal contract. `limit_per_input` is an upper
bound, not a completeness guarantee. The response therefore contains every
record returned by that collection, but does not guarantee complete profile
history; narrower date windows do not establish completeness. The separate
Bright Data Marketplace Dataset filtering API is not part of this live Adapter
and must be evaluated as a distinct source before it can contribute records.

The Apify adapter runs `harvestapi/linkedin-profile-posts` once per Account. It
passes the inclusive request start date as `postedLimitDate`, includes reposts
and quote posts, and disables reaction/comment collection. It sends no Actor
charge limit. It sends `maxPosts=0` as the Actor's unlimited sentinel because
omitting `maxPosts` activates the provider default and can truncate the dataset.
The Actor has no end-date input, so the complete inclusive date window is
enforced locally after the complete dataset is validated. Dataset items are read
page by page until exhausted, with no total item limit. The run has a 30-minute
polling limit. Apify can provide
substantially deeper first-time history than the Bright Data feed, but the
requested range and provider result still remain subject to the Actor's upstream
availability and provider contract. Actor request metadata such as `query`, page,
sort, and session values is never written to a Platform Post. A structured
provider `header` object is post content, not an HTTP header; it is retained only
after the same recursive credential and transport-metadata checks as all other
content.

The Apify X adapter chooses its Xquik route from explicit snapshot lifecycle
state rather than inferring it from dates. An Account absent from the previous
snapshot uses `profileReplies` for its initial provider-visible backfill. An
existing Account uses `mode=search`, `queryType=Latest`, and
`from:<handle> since:<start> until:<end+1-day>` for its incremental window; the
extra day converts the application's inclusive end date to X search's exclusive
`until` boundary. Both routes request rich, nested camelCase records without
`maxItems`, validate the provider dataset, and enforce the inclusive UTC window
locally. The bounded search materially reduces recurring delivery cost and
latency, but observed `source_exhausted` searches can still omit replies. It is
therefore a deliberate cost-first incremental route, not an absolute
completeness guarantee.

The run request also omits `maxTotalChargeUsd`; pricing and budget approval are
operational controls rather than client-side truncation mechanisms. Xquik
1.12.65 and later store their completion report under `run-report` and may use
`outcome=partial` even when a selected route finishes normally.
The adapter accepts `outcome=complete` or `outcome=partial` with zero failed
subtargets when `completionReason=source_exhausted` or
`completionReason=pagination_safety_limit`. A pagination safety limit is a
deliberate best-effort result: accepted rows are published, and a validated
empty dataset maps to no new Posts, even though the search did not prove source
exhaustion. Both completion paths require report counts to exactly match the
downloaded dataset, no diagnostic rows, and no nonzero anomaly count. A
validated `zero-output` report plus its single diagnostic also maps to an empty
Post tuple; an exhausted nonzero outcome with an empty dataset remains
incomplete. Budget-limited, failed-subtarget, count-mismatched,
diagnostic-mixed, or unknown results are rejected as accepted-run failures, so
the Router does not start a second paid fallback.
Rich tweet content remains open after recursive credential and transport-field
rejection; canonical engagement keys are `bookmarks`, `likes`, `quotes`,
`replies`, `reposts`, and `views`.

## Snapshot storage and atomic candidate creation

Snapshots have one root and the exact layout below. Paths are stable and all
records are deterministic JSON.

```text
<snapshot-root>/
├── accounts/<encoded canonical Account ID>.json
├── accounts.json                         # profile_url → record path map
├── posts.json                            # newest-first Post locator list
├── state.json                            # account progress and media retry queues
├── media/<platform>/<post_id>/<scope>/<index>.webp-or-webm
└── posts/<platform>/<encoded runtime Post ID>.json
```

`<platform>` is currently `linkedin` or `x`. Existing LinkedIn bytes and paths
remain unchanged; the Apify X adapter writes canonical records under `posts/x/`.

There are no derived feed, source-copy, or manifest files. `posts.json` contains
one entry for every Post file, sorted by descending `published_at` and then a
deterministic identity tie-breaker. Every entry has `path`,
`account_profile_url`, `published_at`, and `platform`; an empty snapshot writes
`{ "posts": [] }`. Verification reloads the complete tree, regenerates both
indexes and all record paths, and rejects inventory, index, schema, ownership,
or byte inconsistencies. Counts and a SHA-256 digest of the complete tree are
calculated with bounded streaming for explicit verification and the GitHub path,
but are not persisted into the collected dataset. Routine Docker refreshes skip
historical media content reads and report no whole-tree digest. Their private
candidates contain JSON and new media only; validation resolves existing media
against the public snapshot directory.

See [media-and-docker.md](media-and-docker.md) for the shared archival contract,
human-editable business state, and independent local publisher/scheduler.

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

For typed input/`NOT_FOUND` and terminal provider failures, the current
production CLI contract states that `failed_account_ids` contains canonical
requested LinkedIn URLs or canonical requested X profile URLs. Account URLs are
the intentionally observable identifiers in that field; credentials,
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
