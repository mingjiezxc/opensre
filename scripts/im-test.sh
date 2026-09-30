#!/usr/bin/env sh
# Shared test/lint harness for the OpenSRE IM work (im-core / teams-bot / feishu-bot).
#
# The host has no uv and no Python 3.11+, so everything runs inside the
# opensre-sre-agent image (uv 0.12.20, Python 3.11.16), which is already present
# because the local stack is up. Requires outbound access to PyPI, which goes
# through the site HTTP proxy.
#
# Usage (from the repo root):
#   sh scripts/im-test.sh check <dir>   # ruff + pytest for one package
#   sh scripts/im-test.sh ruff  <dir>
#   sh scripts/im-test.sh pytest <dir>
#
# Examples:
#   sh scripts/im-test.sh check im-core
#   sh scripts/im-test.sh check teams-bot
#   sh scripts/im-test.sh check feishu-bot

set -eu

MODE="${1:-check}"
TARGET="${2:-im-core}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec docker run --rm --user root \
  -v "$REPO_ROOT:/work" -w "/work/$TARGET" \
  -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
  -e UV_LINK_MODE=copy \
  -e http_proxy=http://192.168.188.1:3128 \
  -e https_proxy=http://192.168.188.1:3128 \
  -e no_proxy=localhost,127.0.0.1 \
  --entrypoint sh \
  opensre-sre-agent -c '
    set -e
    case "'"$MODE"'" in
      ruff)
        uv run --with ruff ruff check --config /work/ruff.toml . --output-format concise
        ;;
      pytest)
        uv run pytest -q --no-header -p no:cacheprovider
        ;;
      *)
        uv run --with ruff ruff check --config /work/ruff.toml . --output-format concise
        uv run pytest -q --no-header -p no:cacheprovider
        ;;
    esac
  '
