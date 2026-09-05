FROM ghcr.io/prefix-dev/pixi:0.59.0 AS runtime

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY pixi.toml pixi.lock pyproject.toml ./
RUN pixi install --locked && pixi clean cache --yes

COPY src ./src
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod -R a+rX /app/src && chmod 644 /app/docker-entrypoint.sh
ENV PATH="/app/.pixi/envs/default/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUID=1000 PGID=1000

VOLUME ["/data", "/state"]
ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
CMD ["python", "-m", "social_media_subscriber", "serve"]
