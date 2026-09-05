# Operations Runbook

## Scope and safety boundary

Operate this collector only under an approved policy covering the accounts,
provider plan, retention, redistribution, and jurisdiction involved. Use only
organization-authorized provider credentials and public account locators that
the policy permits. Do not use this project to evade platform restrictions,
authentication, rate limits, access controls, provider terms, or privacy law.

Before a pilot or ongoing job, record the policy owner, approved account set,
provider budget/credit ceiling, allowed date range, retention/deletion rules,
and incident contact in your approved internal system. A pilot must pass these
gates before secrets are configured: explicit legal approval, provider-contract
approval, named operator, bounded account list, bounded run frequency, and a
credit alert/stop threshold. Expanding the pool, cadence, platform, or data use
is a new approval decision, not a retry. The presence of an X adapter does not
authorize X collection or data use.

This runbook intentionally contains no provider key, live account URL, or
personal information. Never add any of those to an issue, pull request, shell
history, CI log, artifact, snapshot fixture, or repository file.

## Local setup

Use Pixi only. The committed environment is `default` at
`.pixi/envs/default`, with Python 3.13. Run all project commands from the
repository root:

```sh
pixi install --locked
pixi run subscriber --help
pixi run schemas-check
pixi run verify
```

Expected signals: install resolves the locked environment; help lists exactly
`collect`, `enrich-x-media`, `verify-snapshot`, `publish-dist`, `refresh-local`, and
`serve`; schema check leaves no schema
diff; and `verify` exits `0`. If `schemas-check` reports a diff, do not discard
it blindly—inspect it and either regenerate/commit the intended contract change
or restore the known-good worktree according to your normal review process.

The test suite uses synthetic data. Passing local verification does not authorize
a live provider call, does not authorize publication, and does not perform a remote
cutover. Those operations require separate approval.

The persisted data documentation and repository contracts can be checked
without a provider, credential, Git remote, or publication target. From the
repository root, these are copy-pastable offline Pixi commands:

```sh
pixi run test tests/unit/test_documentation_contract.py -k persisted_data_docs -q
pixi run schemas-check
pixi run verify
```

All three commands must exit `0`; `schemas-check` must leave the tracked schema
files unchanged. These checks do not authorize a live provider call, do not
authorize publication, and do not perform a remote cutover.

Safe local snapshot inspection is read-only:

```sh
pixi run subscriber verify-snapshot /absolute/path/to/snapshot
```

It exits `0` with JSON counts/digest when valid and `5` on any snapshot
integrity failure. It does not contact a provider or Git remote.

## Secrets: comma/newline lists, non-repository handling

`ACCOUNTS` and `SOURCES` accept comma-delimited or newline-delimited values,
including mixed separators. Surrounding whitespace and empty entries are ignored.
Commas are always delimiters; CSV quoting and escaping are not supported.
For `ACCOUNTS`, each entry
must be an approved public LinkedIn person/company or X profile locator;
credentials and query parameters are rejected. Each non-empty `SOURCES` entry must follow
`<source_id>:<api_token>`. The parser splits only the first colon, so provider
tokens may contain additional colons. Source IDs are case-normalized, unknown
IDs reject the whole input, and exact source/token duplicates keep only their
first occurrence. The current allowlist contains `apify` and `brightdata`. Line
order is preserved within each provider. LinkedIn collection always tries every
configured Apify credential before Bright Data, regardless of how the two source
IDs are interleaved. Repeating either source ID with a different token creates
another independent fallback instance. Do not put either value in a shell
command, shell history, `.env.local`, tracked file, or a workflow input.

Apify composes both LinkedIn and X adapters; Bright Data remains LinkedIn-only.
An X-only `collect` run requires at least one approved Apify credential. A new
X Account uses a complete Xquik `profileReplies` run; an existing X Account uses
a bounded `Latest` search. Both routes may consume credits, so this is not a
capability-only boundary check.

Prepare files outside the repository with restrictive permissions, populate them
only through a trusted local editor or secret manager, and use shell redirection
so the secret is not an argument. The following commands are copy-pastable;
they deliberately create empty temporary files and do not invent account or source
values:

```sh
umask 077
accounts_file="$(mktemp -t subscriber-accounts.XXXXXX)"
sources_file="$(mktemp -t subscriber-sources.XXXXXX)"
"${EDITOR:?set EDITOR to a trusted editor}" "$accounts_file"
"${EDITOR:?set EDITOR to a trusted editor}" "$sources_file"
gh secret set ACCOUNTS < "$accounts_file"
gh secret set SOURCES < "$sources_file"
```

Run these only from the intended GitHub repository context after confirming the
GitHub CLI's authenticated account and repository selection. GitHub CLI reads
the secret from standard input; consult the official GitHub Actions
[secret guidance](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions)
before granting access. Do not echo, print, `cat`, or commit either temporary
file. When the secret manager has confirmed storage, move the temporary files to
your operating system's Trash/Recycle Bin (not into this repository) and empty
it according to the approved retention policy. If files were accidentally made
inside the repository, remove them through the approved recovery path, check
`git status --short`, and rotate any value that was staged, committed, logged,
or otherwise exposed.

`.env`, `.env.*`, and `.env.local` are ignored by this repository, but ignored
does not mean safe: environment files are easy to source, copy, back up, and
leak. Prefer the CI secret store and an approved local secret manager. Never
commit `.env.local`; run the secret scan before any commit:

```sh
python /home/ubuntu/.codex/skills/secret-guard/scripts/scan_secrets.py tracked
python /home/ubuntu/.codex/skills/secret-guard/scripts/scan_secrets.py gitignore
python /home/ubuntu/.codex/skills/secret-guard/scripts/scan_secrets.py staged
```

Each clean scan exits `0`; any finding exits `1` and blocks a commit until it is
remediated and rescanned. `staged` intentionally scans only staged paths.

## GitHub Actions behavior and least privilege

The collection workflow has a UTC cron schedule, `17 3 * * *`, and
an input-free `workflow_dispatch` trigger. Both scheduled and manual workflow
runs use the automatic per-account window policy; the workflow does not expose
date overrides. Production collection runs once per day for the approved
accounts in `ACCOUNTS`. New LinkedIn Accounts are backfilled from `2003-05-05`;
new X profiles are backfilled from `2006-03-21`; existing accounts use an
inclusive window from the UTC run date minus three days through the run date,
independent of the latest post and the last successful collection timestamp.
New X profiles use complete `profileReplies`; existing X profiles use bounded
`Latest` search with an exclusive next-day `until` boundary. The client applies
the inclusive window locally on both routes. The adapter sends neither
`maxItems` nor `maxTotalChargeUsd`. It accepts source exhaustion and Xquik's
bounded `pagination_safety_limit`; the latter publishes validated rows, or no
new Posts for a validated empty dataset, without proving that the search window
is complete. Even a search report with `source_exhausted` has been observed to
omit replies, so recurring X collection is an approved best-effort,
cost-first tradeoff. Read GitHub's
official [workflow events documentation](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows)
for schedule/manual trigger behavior.

Workflow permissions are intentionally split:

| Job | `contents` permission | Purpose |
| --- | --- | --- |
| CI | `read` | Verify source on push/pull request. |
| Collection preflight | `read` | Detect whether both runtime secrets are available. |
| Collection publication | `write` | Verify, collect, and update the leased `dist` snapshot only. |

A scheduled run with `ACCOUNTS` or `SOURCES` absent reports a successful
disabled collection. A manual run with either one absent fails preflight. Collection
serialization uses the `social-media-subscriber-dist` concurrency group and
does not cancel an in-progress job. Do not change the job permission or
concurrency policy to make an ad-hoc test easier.

## Collection operation and exit response

Collection and verification emit a JSON summary on standard output. Treat the
process status and `exit_code` together; do not scrape provider text.

| Exit | Meaning | Operator action |
| --- | --- | --- |
| `0` | Successful collection, verification, or publication. | Inspect the summary; unchanged snapshots and best-effort enrichment misses are normal outcomes. |
| `2` | Invalid configuration, account locator, source definition, or date window. | Correct the input before retrying. |
| `3` | Provider pool exhausted before a candidate could be built. | Check authorized provider capacity and status before retrying. |
| `4` | Partial collection with a valid candidate, including media failures. | Publish the valid candidate and investigate failed accounts or the media retry queue. |
| `5` | Integrity, schema, merge, or storage abort. | Preserve redacted diagnostics and investigate before retrying. |
| `6` | Publication rejection or failure, including a stale lease. | Observe the current `dist` and start a new run; do not reuse a stale candidate. |

For Docker worker overlap, timeout, and shutdown behavior, see
[media-and-docker.md](media-and-docker.md).

### Persisted data operator contract

Each approved input canonicalizes to the Account key. `profile_url` is the only
persisted Account identity, and each `Post.account_profile_url` must equal that
exact canonical LinkedIn or X URL. Runtime code exposes derived `Account.id`,
`Post.id`, and `Post.content_hash` properties without duplicating them in JSON.
A changed LinkedIn slug or X handle is a distinct Account, not a rename. Alias
reconciliation and entity merging are not supported here.
Downstream consumers own those cross-URL decisions.

Every Bright Data record must include at least one of
`use_url, user_url, profile_url, and company_url`. The collector parses every
supplied actor URL, rejects a wrong kind or disagreement between fields, and
requires the one resulting canonical URL to equal the requested Account.
`user_id` is optional provider payload data only; it is not consulted for
ownership, routing, discovery, or merging. Apify records use
`query.targetUrl` for the same strict ownership check and never persist that
Actor request metadata. `author` remains content because a repost can identify
its original author instead of the subscribed Account. The LinkedIn
`activity` URN prefix is removed from post IDs so equivalent Bright Data and
Apify activity records merge as one Platform Post; other URN namespaces remain
distinct.

The persisted dataset contains `accounts.json`, `posts.json`, Account records,
and unified Platform Post records under `posts/<platform>/`. `posts.json` is a
newest-first complete list of Post paths with owner URL, publication time, and
platform; it is an index, not a feed. The dataset does not contain a feed,
provider source copy, or snapshot manifest. A successful response with zero
records creates the requested Account and `{ "posts": [] }` when there is no
prior Post history.
Reply, repost, quote, media-only, and unknown provider post types are retained.
A typed `NOT_FOUND` does not create a new Account; on refresh, prior history is
retained.

LinkedIn activity IDs use one feed-update canonical URL, timestamps use UTC
whole-second precision, and positive repost markers correct a generic `post`
classification. Common content is exposed through sparse `text`, `images`,
`videos`, `links`, `author`, `engagement`, `document`, and `repost` keys. Media
lists use objects with a `url` key; optional provider attributes remain inside
those objects. Unknown safe content is retained, but transport and credential
metadata is never persisted.

For typed input/`NOT_FOUND` and terminal provider failures,
`failed_account_ids` contains canonical requested account URLs. Current provider
failures contain canonical requested LinkedIn URLs or canonical requested X
profile URLs. An integrity, actor-ownership,
provider-schema, batch-coverage, duplicate-payload, or referential conflict
aborts the whole candidate before promotion. No candidate or candidate counters
are exposed, and the prior snapshot remains byte-identical.

`collect` requires a prior snapshot directory and a separate candidate output
directory. It contacts the provider and may consume credits:

```sh
pixi run subscriber collect \
  --previous-snapshot /absolute/path/to/verified-prior-snapshot \
  --output /absolute/path/to/candidate-snapshot \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD
```

This command supports both platforms, but executing it for X is a live paid
provider action and requires explicit X scope, account, date-window, and budget
approval. Do not substitute an X locator into a LinkedIn-only operational smoke
approval.

Omit both date options only for the default policy. A new LinkedIn Account starts
at `2003-05-05`; a new X profile starts at `2006-03-21`. An Account already
present uses the inclusive range
from the UTC run date minus three days through the run date, even when it has no
persisted Posts. A complete explicit pair replaces all per-account defaults.
First-time backfills may take substantially longer and consume more provider
credits. Both providers have a 30-minute wait limit and require a valid complete
JSON result without embedded provider errors. Bright Data explicitly requests
up to 1,000 results per Account and only owner-authored posts for personal
profiles. Its profile discovery exposes no cursor, offset, page token, or
reliable truncation marker. The requested limit is not a completeness guarantee,
and the provider does not guarantee complete profile history. Narrower date
windows do not establish completeness and can return account-level provider
errors even when a broad window succeeds. Treat a first-time Bright Data result
as the currently discoverable public feed subset, not as proof of a complete
historical backfill.

Apify runs `harvestapi/linkedin-profile-posts` separately for each Account and
passes the request start date as `postedLimitDate`. It includes reposts and quote
posts, disables comments and reactions, and enforces the complete inclusive date
window locally. It sends no Actor charge limit and sends `maxPosts=0` as the
Actor's unlimited sentinel; omitting `maxPosts` activates the provider default
and can truncate the dataset. It then reads dataset pages until exhausted without
a total item limit. Once an Actor run may have been accepted, a later timeout,
polling, dataset, or schema failure does not fail over to another token or
provider during that Account attempt; retry only through a new approved
collection run. Do not execute this command for exploratory CLI testing: it is a
live provider action.

For X, Apify runs `xquik/x-tweet-scraper` separately for each profile with rich
nested output. New Accounts use `profileReplies`; existing Accounts use bounded
`Latest` search for `from:<handle> since:<start> until:<end+1-day>`. Neither
route sends `maxItems` or `maxTotalChargeUsd`. The client validates the returned
provider dataset and applies the requested inclusive UTC date window locally.
The incremental search route can omit replies even when it reports source
exhaustion, so it is a cost control rather than a completeness boundary.
Publication requires the Actor's `run-report` record to show zero failed
subtargets and either `source_exhausted` or `pagination_safety_limit`, plus exact
report/dataset row counts and zero reported anomalies. Source-exhausted
nonzero outcomes require a nonempty tweet dataset. A pagination safety limit is
accepted as best effort and may publish a validated empty dataset, even though
it does not prove window completeness. Current Actor versions may pair either
completion reason with `outcome=partial`, so the adapter accepts that outcome as
well as `outcome=complete`; budget-limited and unknown completion reasons remain
incomplete. A strict zero-output report and its single diagnostic map to an
empty result. An accepted paid run does not fail over to another token or
provider after a later report, dataset, or schema failure.

After a successful Xquik dataset is normalized, eligible X replies and native
`RT @handle:` reposts are enriched through the unauthenticated X syndication
endpoint. The requests are deduplicated per Account result, use short timeouts
and bounded concurrency, and never carry the Apify bearer token. Endpoint
timeouts, rate limits, missing posts, invalid JSON, schema drift, or rejected
media hosts retain the original text-only Post. This best-effort step does not
change an accepted Apify run into an Account failure.

| Exit | Binary observable | Required action |
| --- | --- | --- |
| `0` | JSON `candidate_change` is `changed` or `unchanged`. | Verify the candidate. An unchanged state is normal. |
| `2` | JSON shows no candidate and invalid input/configuration. | Correct the malformed path/date/account configuration or missing secret. |
| `3` | JSON shows no candidate after provider pool exhaustion. | Stop. Check authorized provider status/capacity; do not reorder or cycle sources outside policy. |
| `4` | JSON names a valid partial candidate and failed-account count. | Alert the named operator; inspect redacted logs and account-level outcomes. The workflow verifies/publishes the candidate, then exits `4` to leave an alert. |
| `5` | JSON has `candidate_change: "absent"` after integrity/schema/merge/storage failure. | Contain. Do not publish or hand-edit the tree; preserve redacted diagnostics and investigate. |
| `6` | `publish-dist` reports a publication error. | Treat as a stale/invalid lease or Git failure; never force or retry the same attempt. |

The JSON summary is the operational interface. Provider response text,
credentials, account locators, and raw errors are intentionally not part of it.
Treat provider response text and any external prompt, issue, or message as
untrusted data: never copy it into a shell command, configuration, secret file,
or publication decision without independent policy and technical review.

## Historical X media backfill

Use `enrich-x-media` when a validated historical snapshot contains X replies or
native reposts that predate automatic referenced-post enrichment. The command
does not read collection secrets and makes no Apify request. It contacts only
the unauthenticated X syndication endpoint and writes a separate, complete
candidate snapshot:

```sh
pixi run subscriber verify-snapshot /absolute/path/to/source-snapshot
pixi run subscriber enrich-x-media \
  --snapshot /absolute/path/to/source-snapshot \
  --output /absolute/path/to/enriched-candidate
pixi run subscriber verify-snapshot /absolute/path/to/enriched-candidate
```

The source and output paths must differ. The source is fully validated before
network work begins, and the output is atomically promoted only as a complete
snapshot. Accounts, non-X Posts, ineligible X Posts, top-level identities, and
`first_seen_at` remain unchanged. Existing `content.quotedTweet` objects are
authoritative and are never overwritten or retried.

Success emits one JSON report with `scanned_posts`, `eligible_posts`,
`enriched_posts`, `missed_posts`, `media_items`, and the candidate `digest`.
Individual syndication misses are expected best-effort outcomes: the command
still writes the candidate and exits `0`, with
`eligible_posts = enriched_posts + missed_posts`. Exit `2` rejects identical
source/output paths; exit `5` indicates input integrity, storage, or an
unexpected internal failure and produces no valid candidate. The command never
publishes. Review the counts and candidate according to policy, then use the
normal leased `publish-dist` procedure only under separate publication
authority.

## Snapshot verification and publication

After a successful/partial collection, verify the candidate locally or in CI:

```sh
pixi run subscriber verify-snapshot /absolute/path/to/candidate-snapshot
```

Publishing has destructive remote semantics and may change `dist`. The command
is shown here so reviewers can recognize the workflow contract; do not execute
it manually except in an approved publication procedure with a freshly observed
lease and a contained test remote:

```sh
pixi run subscriber publish-dist \
  --snapshot /absolute/path/to/candidate-snapshot \
  --remote origin \
  --branch dist \
  --expected-sha <freshly-observed-dist-SHA-or-absent>
```

The only accepted branch is `dist`. Before it writes, the publisher validates
the candidate and the exact prior snapshot, observes the remote `dist` ref,
then compares it with `--expected-sha`. A mismatch exits `6`; it has no retry or
plain-force fallback. A changed candidate is committed as a new root commit,
then pushed with the matching force-with-lease. An unchanged candidate still
checks the lease and reports JSON `result: "unchanged"` with exit `0`.

Do not use an unleased Git force-push, change the expected SHA by hand, or alter a
candidate to resolve a lease conflict. Start a new run from the current remote
snapshot instead. This preserves the one-root `dist` contract and prevents a
stale collector from overwriting newer data.

## Operator-only manual live smoke

Live smoke collection is opt-in; it is never CI behavior, a default validation,
or a substitute for tests. Before starting, an authorized operator must confirm
all of the following in the approved change record:

1. Exactly one approved public LinkedIn account is in the temporary `ACCOUNTS`
   file; the actual account locator is not placed in tickets, logs, docs, or
   shell history.
2. The manual `--start-date` and `--end-date` are a narrow, explicitly approved
   UTC range; both are present and ordered.
3. The provider credit/billing warning has been reviewed, the current balance
   is sufficient, and the operator has authority to spend the expected credit.
4. The candidate output and prior snapshot are in an approved temporary
   location outside the repository. No publication command is scheduled.

This checklist remains LinkedIn-only. The X adapter's presence does not extend
this approval to X; an X live smoke still requires a separate approved
procedure with a current pricing review, budget approval, and completion-report
review.

Immediately before the opt-in run, check the redaction boundary without printing
secret contents: confirm the two environment variables are set in the current
process, confirm the account/source files are outside the repository, and capture
only `git status --short`, command exit code, and the emitted redacted JSON
summary. If the credit gate is not explicitly approved, the process hangs, the
provider returns unexpected text, or the date/account scope differs from the
approval, cancel/terminate the attempt and do not retry.

After the run, verify only the candidate with `verify-snapshot`, record the
exit category and digest/counts, remove temporary candidate data according to
the retention policy, Trash the temporary secret files, unset the two
environment variables, and confirm the source worktree is clean:

```sh
unset ACCOUNTS SOURCES
git status --short
```

If a secret, locator, or raw provider body appears in output, treat it as an
incident: stop sharing logs, restrict access, rotate the affected credential,
and follow the organization’s disclosure and retention policy.

## Incidents and recovery

### Stale lease or failed publication

Exit `6` means the result is not publishable. Do not retry `publish-dist`,
force-push, or use a stale expected SHA. Preserve the redacted command result,
inspect the current `dist` state through the normal approved Git workflow, and
start a new collection from the newly verified baseline. A prior candidate may
be useful for investigation but is not a lease replacement.

### Partial, provider, or schema failure

For `4`, investigate the failed account count and run-local instance health
without exposing credentials. For `3`, stop until capacity/authorization is restored.
For `5`, do not patch snapshot files or bypass validation; compare against the
last verified snapshot and capture redacted diagnostic categories. Re-run only
after the cause has been corrected and the policy gate remains valid.

### Contained publication tests

Publication tests must never target a network or project remote. Build a
disposable local bare remote under a containment directory, ensure the test
working directory is inside that containment root, and fail closed if the
remote is HTTP(S), SSH, `git@`, GitHub-hosted, or outside the containment root.
Also isolate temporary directories and neutralize global/system Git
configuration. The test must prove the lease behavior against that local bare
remote and clean it up afterward.

This is a general prevention and containment lesson. It does not assert that
any separate branch or prior publication-test incident is resolved; do not use
this runbook as evidence that an unresolved incident has been closed.

## Adversarial operator checklist

Before closing an operation, record the following without copying secrets or
account PII:

- CLI help still shows only the documented command/options; malformed command,
  incomplete/inverted dates, missing secret, dirty candidate, and invalid
  snapshot behavior map to the documented nonzero exit category.
- A partial `4` changed/unchanged candidate has an operator alert; a `3`/`5`
  candidate is absent; and a `6` result receives no retry/force path.
- A stale lease, remote change, unexpected publication destination, or dirty
  source worktree stops the operation before any destructive remote action.
- Manual live smoke has a documented opt-in, narrow UTC window, one approved
  account, credit gate, cancellation path, redaction check, and cleanup record.
- Any hanging command is terminated using the approved process supervisor; do
  not extend the timeout by repeatedly rerunning a provider call.
