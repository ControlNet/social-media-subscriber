# Social Media Subscriber

Collect LinkedIn and X posts, archive their images and videos, and make them
available to your website. Run on your own server with Docker or automate
collection with GitHub Actions.

## Features

- Collect posts from LinkedIn people/company pages and X profiles.
- Keep historical posts and collect updates incrementally.
- Archive images as WebP and videos as WebM, reusing previously saved media.
- Keep posts available when media downloads fail, with automatic media retries.
- Export posts and media as static files for your existing web server.

LinkedIn supports Apify and Bright Data; X uses Apify. Collection depends on
provider availability and coverage. Use authorized accounts and credentials;
collection can incur provider charges.

## Configuration

Two settings are required:

- `ACCOUNTS`: public LinkedIn or X profile URLs to collect.
- `SOURCES`: provider credentials in `<source_id>:<api_token>` form, using
  `apify` or `brightdata` as the source ID.

Both accept commas, newlines, or a mixture of the two as separators.

Example (placeholders only):

```yaml
ACCOUNTS: "https://www.linkedin.com/in/YOUR_LINKEDIN_PROFILE/,https://www.linkedin.com/company/YOUR_COMPANY/,https://x.com/YOUR_X_HANDLE/"
SOURCES: "apify:YOUR_APIFY_TOKEN,brightdata:YOUR_BRIGHTDATA_TOKEN"
```

Replace every `YOUR_...` value before starting, and remove accounts or providers
you do not need. `ACCOUNTS` takes full profile/page URLs; each `SOURCES` entry
pairs a provider name with its API token, separated by a colon.

## Docker Compose

Edit [docker-compose.yaml](docker-compose.yaml) to set `ACCOUNTS`, `SOURCES`, your
data directory, UID/GID, and schedule. Do not commit real credentials.
The image collects on startup and then daily at **03:17 UTC** by default.

```sh
docker compose pull
docker compose up -d
docker compose logs --tail=50 subscriber
```

The default data directory is `./social-media`. Serve it at `/social-media/`
through your existing web server. Keep the separate `/state` volume private.

## Docker

Prefer a standalone container? Use this instead of Compose:

```sh
mkdir -p ./social-media
docker run -d \
  --name social-media-subscriber \
  --restart unless-stopped \
  --env-file .env.local \
  --env PUID="$(id -u)" \
  --env PGID="$(id -g)" \
  --volume "$PWD/social-media:/data" \
  --volume social-media-subscriber-state:/state \
  controlnet/social-media-subscriber:latest
```

Change the host side of the `/data` mount to use a different data directory.

## GitHub Actions

Set the repository Actions secrets `ACCOUNTS` and `SOURCES`. The **Collect and
publish dist** workflow runs daily at **03:17 UTC**, or manually from the Actions
tab, and saves the collected posts and media to the `dist` branch.

To publish your own Docker image automatically, also configure
`DOCKERHUB_USERNAME` and `DOCKERHUB_PASSWORD`. After checks pass on a default-branch
push, CI publishes `DOCKERHUB_USERNAME/social-media-subscriber:latest`. Without
both secrets, image publication is skipped.

## Local use

Use [Pixi](https://pixi.sh/) to install dependencies and explore the CLI:

```sh
pixi install --locked
pixi run subscriber --help
```

For development checks:

```sh
pixi run verify
```

## Documentation

- [Docker deployment and media management](docs/media-and-docker.md): scheduling,
  storage, retries, and recovery.
- [Operations guide](docs/operations.md): provider setup, CLI commands, GitHub
  Actions, and troubleshooting.
- [Architecture](docs/architecture.md): data formats, provider integration,
  validation, and publishing internals.
