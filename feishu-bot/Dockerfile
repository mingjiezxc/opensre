# feishu-bot/Dockerfile
#
# BUILD CONTEXT IS THE REPO ROOT, because this service depends on the sibling
# im-core/ package (an editable path dependency, ../im-core):
#
#   docker build -f feishu-bot/Dockerfile -t opensre-feishu-bot .
#
# Hence both trees are copied side by side under /app so the relative path
# ../im-core still resolves after the copy.
FROM python:3.11-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY im-core/ /app/im-core/
COPY feishu-bot/ /app/feishu-bot/
# Defensive: a developer's local .venv/build cache must never be baked into the
# image. (The repo-root .dockerignore normally excludes it; this does not depend
# on that.)
RUN rm -rf /app/feishu-bot/.venv /app/feishu-bot/.pytest_cache \
    /app/feishu-bot/.ruff_cache /app/feishu-bot/.sdk-inspect
WORKDIR /app/feishu-bot
RUN uv sync --frozen --no-dev || uv sync --no-dev
EXPOSE 3979
CMD ["uv", "run", "python", "app.py"]
