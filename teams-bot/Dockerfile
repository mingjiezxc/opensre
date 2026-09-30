# teams-bot/Dockerfile
#
# Build context is the repository root, e.g.
#   docker build -f teams-bot/Dockerfile .
#   # docker compose: context: .  +  dockerfile: teams-bot/Dockerfile
# because this bot depends on the sibling local package im-core
# ([tool.uv.sources] opensre-im-core = { path = "../im-core" }).
FROM python:3.11-slim
WORKDIR /work
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# Keep the teams-bot/ + im-core/ sibling layout so the relative path source in
# teams-bot/pyproject.toml resolves. Copy sources before syncing: uv resolves
# the editable path dependency from ../im-core.
COPY im-core ./im-core
COPY teams-bot ./teams-bot
RUN cd /work/teams-bot && (uv sync --frozen --no-dev || uv sync --no-dev)
WORKDIR /work/teams-bot
EXPOSE 3978
# Invoke the interpreter directly rather than through `uv run`.
# charts/opensre/templates/teams-bot.yaml pins
#   command: ["/app/.venv/bin/python", "app.py"]
# so the venv must exist at that path even though the sources live under /work.
# Running the interpreter also keeps `uv` out of the runtime path, which matters
# for read-only-root deployments.
RUN mkdir -p /app && ln -sfn /work/teams-bot/.venv /app/.venv
CMD ["/app/.venv/bin/python", "app.py"]
