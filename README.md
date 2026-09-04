# Social Media Subscriber

`social-media-subscriber` builds a deterministic, provider-neutral snapshot of
authorized public social-media accounts. The domain, runtime, schemas, and
snapshot layout support LinkedIn accounts and X profiles. Production source
composition supports LinkedIn through Apify and Bright Data, and X profiles
through Apify's Xquik Actor.
It is an operations tool, not a browser automation service; collection uses an
approved provider adapter, normalizes the response into canonical records, and
can publish one validated snapshot to the repository's `dist` branch.

Use it only for accounts and provider credentials that your organization is
authorized to use. The service does not bypass platform controls, authentication,
access restrictions, robots directives, contractual limits, or applicable law.
See [operations](docs/operations.md) for the required policy gates and
[architecture](docs/architecture.md) for record and publication contracts.

## Quick orientation

There are four CLI commands, all run through the repository's Pixi `default`
environment (Python 3.13):

```sh
pixi install --locked
pixi run subscriber --help
pixi run subscriber collect --help
pixi run subscriber enrich-x-media --help
pixi run subscriber verify-snapshot --help
pixi run subscriber publish-dist --help
```

`collect` needs the multiline `ACCOUNTS` and `SOURCES` environment variables
and performs provider I/O. Each non-empty `SOURCES` line has the form
`<source_id>:<api_token>`. The supported source IDs are `apify` and
`brightdata`; LinkedIn collection always tries all configured Apify instances
before Bright Data. Repeating either source ID with a different token creates
another independent fallback instance, preserving line order within that
provider.
Each configured Apify credential composes independent LinkedIn and X adapters;
Bright Data remains LinkedIn-only. A new X Account uses one complete Xquik
`profileReplies` run for its initial backfill. An X Account already present in
the previous snapshot uses a bounded `Latest` search for the requested date
window, which lowers incremental cost and latency but can omit replies. Both
routes enforce the inclusive date window locally and require strict completion
evidence. Neither route sends an item limit or run-level USD charge limit.
After an accepted Xquik result is normalized, the X adapter makes short-lived,
unauthenticated syndication requests for referenced posts in replies and native
`RT @handle:` reposts. That best-effort step adds `content.quotedTweet` when the
referenced post is valid. It does not make another Apify request, does not send
the Apify token to syndication, and never turns an accepted Xquik result into a
collection failure.
`enrich-x-media` performs the same best-effort operation over one validated
historical snapshot and writes a separate complete candidate. It does not read
`ACCOUNTS` or `SOURCES`, call Apify, verify the candidate for publication, or
publish it.
`verify-snapshot` is local and read-only.
`publish-dist` mutates the selected Git remote and must be used only under the
immutable lease described in the runbook; do not run it as a casual local test.

## Persisted data boundary

Account identity is the exact canonical public account URL returned by the
strict locator parser: a LinkedIn person/company URL or an X profile URL.
`profile_url` is the only persisted Account identity field. Runtime code exposes
the same value as `Account.id` for routing and merge operations, but does not
duplicate it in JSON. Each Post uses `account_profile_url` for ownership. A
changed LinkedIn slug or X handle produces a distinct Account, so both URL-keyed
histories may coexist. Alias reconciliation and entity merging belong in the
consuming system.

Every successful Bright Data LinkedIn record must supply at least one actor
field from `use_url, user_url, profile_url, and company_url`. The LinkedIn
collector parses every supplied actor URL, requires one requested person/company
kind and canonical URL, and requires that owner to equal the requested Account
URL. Provider `user_id` is optional provider payload data only; it is never
identity or an ownership fallback. The Apify X adapter independently validates
the Actor author's canonical handle and each numeric status URL against the
requested X profile before persistence.

A successful response with zero records still persists the requested Account
with no Posts. Reply, repost, quote, media-only, and provider-defined post types
are retained rather than filtered. A typed `NOT_FOUND` is different: it does not
create a new Account, and a failed refresh preserves existing history. For
account-scoped and terminal provider failures, `failed_account_ids` contains
canonical requested account URLs. Current provider failures therefore contain
canonical requested LinkedIn URLs or canonical requested X profile URLs.
Integrity, ownership, schema, coverage, or conflict
failures abort the whole candidate, suppress candidate counters, and the prior
snapshot remains byte-identical.

This repository revision was verified offline with synthetic data. It does not
authorize a live provider call, does not authorize publication, and does not
perform a remote cutover. Those operations require separate, fresh approval.

## What a successful run produces

The output is a complete, verified snapshot directory, not a patch over a
working directory. Its canonical tree is:

```text
snapshot/
├── accounts/
│   └── <sha256(profile_url)>.json
├── accounts.json
├── posts.json
└── posts/<platform>/
    └── <sha256(platform post identity)>.json
```

The supported directories are `posts/linkedin/` and `posts/x/`. The Apify
source can populate both; Bright Data can populate only `posts/linkedin/`.

`accounts.json` is a direct `profile_url` to account-record path map. Account
records contain only `platform`, `kind`, `profile_url`, and `first_seen_at`.
`posts.json` contains a newest-first `posts` list with each Post record's path,
owner URL, publication timestamp, and platform. Post records contain fixed
provider-neutral identity, ownership, URL, timestamp, and type fields plus an
open `content` object. LinkedIn adapters use shared canonical keys for text,
image/video objects, links, author, engagement, document, and repost data while
retaining safe future provider fields that have no canonical mapping yet.
For an X reply or flattened native repost, `content.quotedTweet` may contain the
referenced status ID, canonical URL, timestamp, text, author, and filtered media.
Media image URLs are limited to HTTPS `pbs.twimg.com`; media marker URLs are
limited to HTTPS `t.co`; and MP4 variants are limited to HTTPS
`video.twimg.com`. This optional nested content is a compatible extension of
the existing open `content` object. Top-level Post fields, types, paths, and
indexes are unchanged, and older records without it remain valid.
Transport, authentication, request, response, and error metadata are rejected
before persistence.

The collector deliberately does not emit a feed, provider source copy, or
snapshot manifest. `posts.json` is only a complete record locator index, not a
curated feed. Other derived views belong to the backend compiler that consumes
`dist`. `pixi run subscriber verify-snapshot <snapshot>` validates the exact
file inventory, regenerated indexes, record schemas, and ownership before
reporting counts and a derived digest.

The `dist` branch is an immutable snapshot history: every changed publication
is a new root commit with no parent. It intentionally does not retain source
branch history. The source checkout remains untouched by publication work.

## Development checks

From the repository root, the standard local gates are:

```sh
pixi install --locked
pixi run format-check
pixi run lint
pixi run typecheck
pixi run test
pixi run actionlint
pixi run schemas-check
pixi run verify
```

`pixi run verify` runs format, lint, type, test, and GitHub Actions lint checks.
`pixi run schemas-check` generates schemas and exits nonzero if tracked schema
files would change, so begin with a clean worktree. Neither command is a live
provider smoke test or a publication command.

## Exit contract

Collection and verification emit a JSON summary on standard output. Treat the
process status and `exit_code` together; do not scrape provider text.

| Exit | Meaning | Operator action |
| --- | --- | --- |
| `0` | Success. Collection or media enrichment has a valid candidate, or verification/publishing completed. Media enrichment may report nonzero `missed_posts`. | Inspect `candidate_change`, enrichment counts, or publication `result`; `unchanged` and best-effort enrichment misses are normal outcomes. |
| `2` | Invalid CLI input, date window, account locator, or source definition. No candidate exists. | Correct the local input or secret value; do not retry unchanged input. |
| `3` | Provider pool exhausted before a candidate could be built. No candidate exists. | Pause, inspect authorized credential capacity and provider status, then retry only after remediation. |
| `4` | Partial collection with a valid candidate (changed or unchanged). | Treat as an alert. The scheduled workflow verifies and publishes that candidate, then fails visibly so the account-level issue is investigated. |
| `5` | Integrity, schema, merge, or storage abort. No candidate is promoted. | Contain the run, preserve redacted diagnostics, and investigate inputs/provider shape before retrying. |
| `6` | Publication rejection or failure, including stale lease. | Do not retry, force-push, or reuse the candidate. Re-observe `dist` and start a new run. |

## Automation

GitHub Actions has two separate workflows:

- CI runs `pixi run verify` for pushes and pull requests with read-only
  repository permissions.
- Collection is scheduled at `17 3 * * *` (UTC) and can be started manually
  with a complete inclusive `start_date`/`end_date` pair. A scheduled run with
  either provider secret missing exits successfully as disabled; a manual run
  with missing secrets fails preflight. Only the publication job has
  `contents: write`, and only after preflight succeeds.
- By default, an Account absent from the previous snapshot is backfilled from
  its platform boundary: `2003-05-05` for LinkedIn and `2006-03-21` for X. An
  existing Account, including one with zero Posts, is collected from the UTC run
  date minus three days through the run date. New X Accounts use full
  `profileReplies`; existing X Accounts use `Latest` search with
  `since:<start>` and exclusive `until:<end+1-day>`. Search is the intentional
  cost-first incremental route and has demonstrated reply false negatives even
  with `source_exhausted`. Xquik input sets neither an item limit nor a USD
  charge limit; any run that finishes without source exhaustion is rejected as
  incomplete rather than published.

For secret setup, scheduling behavior, incident recovery, and the explicit
operator-only live smoke procedure, use [docs/operations.md](docs/operations.md).
