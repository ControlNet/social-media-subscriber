# Social Media Subscriber

`social-media-subscriber` builds a deterministic, provider-neutral snapshot of
authorized public LinkedIn accounts. It is an operations tool, not a browser
automation service: collection uses the approved provider adapter, normalizes
the response into canonical records, and can publish one validated snapshot to
the repository's `dist` branch.

Use it only for accounts and provider credentials that your organization is
authorized to use. The service does not bypass platform controls, authentication,
access restrictions, robots directives, contractual limits, or applicable law.
See [operations](docs/operations.md) for the required policy gates and
[architecture](docs/architecture.md) for record and publication contracts.

## Quick orientation

There are three CLI commands, all run through the repository's Pixi `default`
environment (Python 3.13):

```sh
pixi install --locked
pixi run subscriber --help
pixi run subscriber collect --help
pixi run subscriber verify-snapshot --help
pixi run subscriber publish-dist --help
```

`collect` needs the `ACCOUNTS` and `BRIGHT_DATA_API_KEYS` environment variables
and performs provider I/O. `verify-snapshot` is local and read-only.
`publish-dist` mutates the selected Git remote and must be used only under the
immutable lease described in the runbook; do not run it as a casual local test.

## What a successful run produces

The output is a complete, verified snapshot directory, not a patch over a
working directory. Its canonical tree is:

```text
snapshot/
├── accounts/
│   └── <canonical-account-id filename>.json
├── accounts.json
├── feed.json
├── posts/linkedin/
│   └── <canonical-post-id filename>.json
├── source/brightdata/linkedin/posts/
│   └── <canonical-post-id filename>.json
└── snapshot.json
```

`snapshot.json` carries record counts and the SHA-256 digest of every
non-manifest file. `pixi run subscriber verify-snapshot <snapshot>` validates
the whole tree, generated indexes, record schemas, ownership, and digest before
reporting its machine-readable result.

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
| `0` | Success. Collection has a valid candidate, or verification/publishing completed. | Inspect `candidate_change` or publication `result`; `unchanged` is a normal outcome. |
| `2` | Invalid CLI input, date window, configuration, or account input. No candidate exists. | Correct the local input or secret value; do not retry unchanged input. |
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

For secret setup, scheduling behavior, incident recovery, and the explicit
operator-only live smoke procedure, use [docs/operations.md](docs/operations.md).
