# Media snapshots and Docker

Both execution paths use the same collector, media archiver, and `state.json`:

- GitHub Actions reads the previous complete `dist`, creates a complete candidate
  including `media/`, and publishes a new parentless `dist` commit. It does not
  connect to the website server. GitHub Pages is not needed for Docker hosting.
- Docker reads the previous snapshot in `/data` and publishes locally. It never
  clones or pushes Git. Apache (or another existing static server) serves that
  directory at `/social-media/`; the container does not run an HTTP server.

There is no mode flag, media index, content-hash filename, or SSH deployment step.
Git's 100 MiB single-file limit still applies to the GitHub path; media is not
silently excluded to work around it. Use Docker for snapshots that exceed Git's
limits. Disabling GitHub Pages and the scheduled collection workflow is an
operator choice, not something the container changes.

## Automatic image publication

Set repository Actions secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_PASSWORD` to
enable image publication. Prefer a Docker Hub access token with repository write
permission as the password value. Do not store either value in source files.

After CI verification succeeds on a push to the default branch, the `docker` job
builds `linux/amd64` and pushes
`DOCKERHUB_USERNAME/social-media-subscriber:latest`. If either secret is absent,
it skips building and publishing without failing CI. Pull requests and other
branches never publish. The password is supplied only to the publication step,
via Docker's standard-input login; it is not a build argument or image layer.
This updates the registry tag only and does not restart deployed containers.

## Archival and retries

Media paths are `media/{platform}/{post_id}/{scope}/{original_index}.{extension}`;
JSON references use `/social-media/media/...`. Once a slot has been
successfully published, its file and owned URL are reused permanently, including
when the provider returns a different signed URL or omits the media on refresh.
Old media is not downloaded or converted again. All retained posts are inspected
for unarchived slots, including historical posts outside the collection window.

By default, `ENABLE_MEDIA_COMPRESSION=false` preserves original media without
conversion. With `ENABLE_MEDIA_COMPRESSION=true`, images, avatars, document
covers, job logos, and video posters become WebP; videos become VP9/Opus WebM at
their original dimensions. Set it to `false` to preserve downloaded bytes without
image re-encoding or FFmpeg invocation. Supported originals are JPEG, PNG, GIF,
WebP, AVIF, MP4, MOV, and WebM; extensions are detected from the downloaded content,
not signed URL suffixes. Unsupported content keeps its source URL and follows the
media failure policy. Changing the setting affects only unarchived slots and retries,
never existing files. X videos and animated
GIFs use one highest-bitrate progressive variant and its poster, including
quoted/referenced-post media. HLS playlists and navigation links are excluded.
No format can guarantee a particular file size or browser compatibility.

A failed media item keeps its source URL; the post is still published. Temporary
transport failures get at most three attempts per run. A slot that fails in three
runs moves from `pending_media` to `failed_media` and stops retrying. A run with
new media failures publishes a valid candidate with exit `4`. Existing permanent
failures do not repeatedly fail otherwise successful runs.

Only expected source/download/decoding/conversion failures consume that retry
budget. Internal program or filesystem failures keep the post and source URL,
remain pending with `error: "internal"`, and do not increment `failed_runs`.
New entries of this kind start at `0`. Logs report the exception type, not its
raw message. Correct the worker problem and the next run retries automatically.
Previously permanent entries still require the manual repair described below.

`state.json` contains:

- `accounts`: canonical account URL to last successful collection timestamp,
  retained for status only. Existing accounts always collect from the current UTC
  run date minus three days through the run date; this does not expand after downtime.
  Failed accounts do not advance; successful accounts advance
  even if some media fails. Explicit date overrides retain their existing behavior.
- `pending_media` and `failed_media`: entries with `post_id` (platform-qualified
  Post ID), `scope`, `index`, `source_url`, `failed_runs`, and a safe `error` category.

Retries run even with no newly discovered posts. If all provider accounts are
unavailable but a previous snapshot has pending media, those media can still be
retried and published without advancing account timestamps. Freshly rediscovered
posts refresh pending source URLs; otherwise the queue's stored URL is used.

For manual repair, stop the scheduler, back up `state.json`, edit the affected
`source_url`, move its entry from `failed_media` into `pending_media`, and set
`failed_runs` to `1`. Keep the slot identifiers unchanged. Ordinary JSON formatting
is accepted for this human-editable file. Restart the scheduler to retry. An
expired source may need a fresh provider URL; retries cannot revive an expired URL.
Do not delete archived files or change their owned paths to request a re-encode.

## Container deployment

The image uses `WORKDIR /app`, public snapshot volume `/data`, and private service
volume `/state`. Host paths are entirely operator-selected. `/state` contains the
lock, scheduler status, collected posts, media work files, and the candidate used
for interrupted publication recovery; never expose it through Apache. Historical
media remains in `/data` and is not copied into the candidate. Back up both volumes.

The included `docker-compose.yaml` uses the published image and bind-mounts
`./social-media` at `/data` and `./state` at `/state`, without Docker named volumes.
Both local directories are ignored by Git. Edit the host paths and the settings
in its `environment` section directly, including `ACCOUNTS` and `SOURCES`.
These two required values contain `YOUR_...` placeholders; replace them with your
account URLs and provider tokens before starting the service.
No environment file is loaded by this Compose configuration. Do not commit a
deployment copy containing real credentials.
The host data path may be an ignored directory inside the website checkout.

Both values accept comma-separated entries, newline-separated entries, or a
mixture. The README's `docker run` command supplies both values inline using
`--env ACCOUNTS='...'` and `--env SOURCES='...'`, with comma-separated entries.
Existing multiline environment variables continue to work with
`--env ACCOUNTS --env SOURCES`.
Commas are always delimiters; CSV quoting/escaping is not supported.

```sh
docker compose pull
docker compose up -d
docker compose logs --tail=50 subscriber
docker compose exec subscriber python -m social_media_subscriber verify-snapshot /data
docker compose exec subscriber python -c 'from pathlib import Path; print(Path("/state/status.json").read_text())'
```

Verification should exit `0`. Status records `running`, `next_run_at`, the last
start/finish/success timestamps, and the worker exit code. Logs never include
provider credentials or raw media download errors. `state.json` is public snapshot
data and intentionally contains source media URLs for failed items.

Optional runtime environment settings (defaults shown):

| Setting | Default |
| --- | --- |
| `CRON_SCHEDULE` | `17 3 * * *` |
| `ENABLE_MEDIA_COMPRESSION` | `false` |
| `TIMEZONE` | Unset: use the host-mounted `/etc/localtime` |
| `REFRESH_ON_STARTUP` | `true` |
| `WORKER_TIMEOUT_SECONDS` | `0` (no deadline) |
| `PUID`, `PGID` | `1000`, `1000` |

Compose and the README's Linux `docker run` example mount the host's
`/etc/localtime` read-only. The scheduler reads its timezone rules, including
daylight-saving changes, so the default job runs at 03:17 host local time.
Remove an existing `TIMEZONE=UTC` setting to inherit the host timezone; explicitly
setting `TIMEZONE` to an IANA name still overrides it. Without the mount, the
container uses its own `/etc/localtime`, not the host's timezone. Recreate the
container after changing the host timezone. Stored timestamps and GitHub Actions
cron schedules remain UTC.

The entrypoint sets ownership of the two volume roots and then drops privileges.
It does not recursively change existing files. Match `PUID`/`PGID` to their owner;
pre-existing inaccessible files need operator correction. Published directories
are `0755` and files `0644`. Source credentials must stay outside `/data`.

Manual execution uses the same lock as scheduled execution:

```sh
docker compose exec subscriber python -m social_media_subscriber refresh-local
```

Exit `75` means another worker holds the lock; no overlapping collection starts.
There is no per-video encoding deadline or default whole-worker deadline. If an
explicit positive `WORKER_TIMEOUT_SECONDS` is set, timeout exits `124`. Set an old
deployment's `7200` value to `0` to disable its previous limit. Shutdown still
terminates the worker and FFmpeg process group, and the lock prevents overlap.

Before media processing, `/state/work/snapshot` saves collected posts and their
existing media references. `/state/work/media/...` holds completed new media;
per-slot `.encoding-<index>` directories hold inputs and unfinished outputs.
A completed download is atomically renamed from `.download` to `.input`. Only a
validated media output receives its final slot filename. The next run resumes the
saved posts without another provider call, reuses completed media and inputs, and
restarts interrupted encodes/downloads rather than treating partial files as valid.
Resumed runs report zero newly collected accounts. Failure queues are committed
with the final snapshot, not after each individual media attempt.

The work directory is removed only after successful publication, including a
publication with media failures retained in `state.json`. Do not remove `/state`
when recreating the container. Old versions' random `/tmp` directories are not
automatically imported into this layout.

### Runtime logs

```sh
docker compose logs -f --tail=100 subscriber
```

Docker logs default to INFO, with UTC timestamps. Routine HTTP debug requests are
hidden. Logs distinguish account collection/windows, provider task status, dataset
counts, media download/retry/reuse, encoding, decode validation, snapshot writing,
publication, and the next scheduled run. Accounts use their existing record filenames
so they can be matched to `accounts.json` without logging input URLs. Media events
carry the post ID, scope, original index, and batch position/total.

Long video encoding and validation emit a heartbeat every 30 seconds with elapsed
time and the latest numeric FFmpeg progress: `frames`, `fps`, `media_seconds`, and
`speed_x`. Missing fields mean FFmpeg has not supplied a valid value yet; these are
not completion percentages. Provider polling logs status changes and a periodic
30-second update instead of every HTTP request. Tokens, signed media URLs, raw
provider responses, and raw FFmpeg diagnostics are not logged.

The command does not specify `-threads`: FFmpeg and its codecs select their default
threading automatically. There is no application-enforced two-thread limit, and
codec auto-detection is not a guarantee of respecting a VM/container CPU quota.

Completed candidates are replayed after interrupted publication. Media publishes
first, then records and indexes, with business state last. Publication is per-file
atomic, not a transaction across all JSON files. Interrupted temporary files use
reserved `.publishing-*` names and are removed during candidate replay.

Allow disk space in `/state` for candidate JSON and newly archived media, plus
temporary conversion input/output. Normal Docker refreshes check media paths,
regular-file existence, and nonzero sizes without reading or hashing historical
media contents. They report `digest: null`; use `verify-snapshot /data` for an
explicit full-content read and snapshot digest. JSON validation remains enabled
on every refresh. GitHub collection still builds and validates a self-contained
snapshot, including all media. Old complete local candidates can also be replayed.

The Astro website fetches `/social-media/posts.json` and records at runtime. It can
build without any snapshot directory. Keep source HTTPS fallbacks supported while
pending media is being repaired. Serve WebP as `image/webp`, WebM as `video/webm`,
and support byte-range requests. JSON should revalidate; avoid long-lived caching
of the mutable post/index files. Do not expose `/state` or `.env.local`.

## Verification without paid collection

```sh
pixi run verify
pixi run test tests/integration/test_media_pipeline.py tests/unit/test_media_download.py -q
docker build --platform linux/amd64 -t controlnet/social-media-subscriber:latest .
docker run --rm --network none controlnet/social-media-subscriber:latest python -m social_media_subscriber --help
```

All checks should pass. Tests use explicitly synthetic provider/media inputs and
real local WebP/WebM conversion; no test calls a paid provider or deploys to Apache.
