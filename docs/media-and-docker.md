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

Media paths are `media/{platform}/{post_id}/{scope}/{original_index}.webp` or
`.webm`; JSON references use `/social-media/media/...`. Once a slot has been
successfully published, its file and owned URL are reused permanently, including
when the provider returns a different signed URL or omits the media on refresh.
Old media is not downloaded or converted again. All retained posts are inspected
for unarchived slots, including historical posts outside the collection window.

Images, avatars, document covers, job logos, and video posters become WebP.
Videos become VP9/Opus WebM at their original dimensions. X videos and animated
GIFs use one highest-bitrate progressive variant and its poster, including
quoted/referenced-post media. HLS playlists and navigation links are excluded.
No format can guarantee a particular file size or browser compatibility.

A failed media item keeps its source URL; the post is still published. Temporary
transport failures get at most three attempts per run. A slot that fails in three
runs moves from `pending_media` to `failed_media` and stops retrying. A run with
new media failures publishes a valid candidate with exit `4`. Existing permanent
failures do not repeatedly fail otherwise successful runs.

`state.json` contains:

- `accounts`: canonical account URL to last successful collection timestamp.
  The next collection starts three days before that timestamp and ends on the
  current UTC date. Failed accounts do not advance; successful accounts advance
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
lock, scheduler status, and complete candidate used for interrupted publication
recovery; never expose it through Apache. Back up both volumes.

Create an ignored `.env.local` using the existing `ACCOUNTS` and `SOURCES` format
documented in [operations.md](operations.md). Do not put these values in Docker
build arguments or image layers. Set `SOCIAL_MEDIA_DATA_DIR` to an absolute host
directory before invoking Compose; it may be an ignored `social-media` directory
inside the website checkout.

```sh
docker compose build
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
| `TIMEZONE` | `UTC` |
| `REFRESH_ON_STARTUP` | `true` |
| `WORKER_TIMEOUT_SECONDS` | `7200` |
| `PUID`, `PGID` | `1000`, `1000` |

The entrypoint sets ownership of the two volume roots and then drops privileges.
It does not recursively change existing files. Match `PUID`/`PGID` to their owner;
pre-existing inaccessible files need operator correction. Published directories
are `0755` and files `0644`. Source credentials must stay outside `/data`.

Manual execution uses the same lock as scheduled execution:

```sh
docker compose exec subscriber python -m social_media_subscriber refresh-local
```

Exit `75` means another worker holds the lock; no overlapping collection starts.
Timeout exits `124`, and shutdown terminates the worker and FFmpeg process group.
Completed candidates are replayed after interrupted publication. Media publishes
first, then records and indexes, with business state last. Publication is per-file
atomic, not a transaction across all JSON files. Interrupted temporary files use
reserved `.publishing-*` names and are removed during candidate replay.

Allow disk space for the complete candidate in `/state` as well as `/data` and
temporary conversion input/output. Historical media is streamed, not held in RAM,
but complete-candidate validation and publication still require local disk I/O.

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
