# feishu-bot

Feishu / Lark bot for OpenSRE incident investigations.

It is a thin adapter: all SSE interpretation, progress rendering, Markdown
parsing, throttling and question-timeout policy live in the shared
[`im-core`](../im-core) package, and this service supplies only the Feishu
rendering and message identity through the core's `PlatformHooks` protocol.
Behaviour is deliberately close to [`teams-bot`](../teams-bot).

See [`docs/FEISHU_SETUP.md`](../docs/FEISHU_SETUP.md) for the app-console setup
(scopes, event subscriptions, permissions).

```
Feishu ──(event / card callback)──▶ feishu-bot ──(SSE /investigate)──▶ sre-agent
                                        │                                  │
                                        └──(POST /answer, /threads/{id}/queue-message)
```

## Layout

| File | Responsibility |
|---|---|
| `app.py` | Entrypoint; long-connection (`ws`) or webhook mode; exits 0 without credentials |
| `config.py` | `FeishuConfig(BaseIMConfig)` — Feishu credentials + shared OpenSRE settings |
| `message_parser.py` | Pure event accessors, @mention detection, `@_user_N` stripping |
| `card_builder.py` | Welcome / progress / question-form / timeout / final cards |
| `card_converter.py` | `IMBlock[]` → Feishu card elements (Markdown rendering) |
| `card_actions.py` | `card.action.trigger` → `/answer`, reusing the core's answer shaping |
| `lark_client.py` | Async wrapper over `lark-oapi`; message send/patch/reply, identity lookups |
| `bot_handlers.py` | Routing, `PlatformHooks`, mid-run queueing, SDK sync adapters |
| `webhook_server.py` | aiohttp routes for webhook mode (token + decrypt checks) |
| `progress_text.py` | Re-export of the core's progress renderer (no copy) |

## Run

```bash
# long connection (default): no public URL, no tunnel
FEISHU_APP_ID=cli_… FEISHU_APP_SECRET=… uv run python app.py

# request-URL mode
FEISHU_MODE=webhook FEISHU_VERIFICATION_TOKEN=… FEISHU_ENCRYPT_KEY=… uv run python app.py
```

Or through the stack: `make dev-feishu` / `docker compose --profile feishu up -d --build`.

The Docker build context is the **repository root** (this service depends on the
sibling `im-core/` via an editable `../im-core` path dependency):

```bash
docker build -f feishu-bot/Dockerfile -t opensre-feishu-bot .
```

## Environment

Required: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`.

| Variable | Default | Notes |
|---|---|---|
| `FEISHU_MODE` | `ws` | `ws` (long connection) or `webhook` |
| `FEISHU_VERIFICATION_TOKEN` | — | webhook only; enforced when set |
| `FEISHU_ENCRYPT_KEY` | — | webhook only; decryption enforced when set |
| `FEISHU_BOT_NAME` | `OpenSRE` | welcome-card title; literal name stripping |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | `https://open.larksuite.com` for the international app |
| `FEISHU_BOT_OPEN_ID` | — | optional override for the startup `GET /open-apis/bot/v3/info` |
| `PORT` | `3979` | webhook mode only |
| `SRE_AGENT_URL` | `http://localhost:8000` | inherited from `BaseIMConfig` |
| `INVESTIGATE_AUTH_TOKEN` | — | bearer token for `/investigate` and `/answer` |
| `WEB_UI_PUBLIC_BASE_URL` | — | origin for the **View in OpenSRE** link |
| `OPENSRE_TEAM_TOKEN` | — | optional; attributes runs to a real org/team |

With no credentials the process logs a warning and exits 0 (never a crash loop).

## Behaviour

- **Groups** require an @mention, compared against the bot's own `open_id` —
  fetched at startup and never hardcoded. The `@_user_N` placeholder is stripped
  before the prompt is sent.
- **Direct messages** need no mention. `help` / `status` return the welcome card.
- **Threading:** `chat_id` → `sanitize_thread_id(…, "feishu")`, so a second
  message in the same conversation continues the same investigation.
- **Progress:** one card, edited in place via `PATCH`, throttled to 1.5s because
  Feishu rate-limits card updates.
- **Questions:** rendered as a Feishu form — `select_static`, or
  `multi_select_static` when the question is multi-select, plus an optional
  comment input. Field names stay `answer_{i}` / `comment_{i}` so the core's
  `shape_answers_from_card_data` is reused unchanged; the submit button carries
  `answer_thread_id`. Option *descriptions* are shown, unlike the Teams card.
- **Timeouts:** the question card is replaced with a timeout notice in place.
- **Mid-run messages:** routed to `/threads/{id}/queue-message`; `404`/`409`
  fall through to a follow-up run on the same thread.
- **Final reply:** a separate card with the report and the **View in OpenSRE** link.

## Tests

```bash
sh scripts/im-test.sh check feishu-bot   # ruff + pytest, inside the dev image
```

Group-chat behaviour is covered with synthetic v2 events, because the bot is not
a member of any live Feishu group. The question-timeout test drives the real
shared runner against a faked aiohttp session, so the `PlatformHooks` contract is
exercised end to end.

## Implementation notes

- `lark-oapi` invokes registered handlers **synchronously**; `bot_handlers`
  therefore registers sync adapters that schedule the async work, and returns the
  card-action toast immediately.
- Every generated SDK message call used here has an async twin
  (`acreate`/`apatch`/`areply`, `contact…aget`, `auth…ainternal`), which is what
  the wrapper calls.
- `GET /open-apis/bot/v3/info` is not modelled by the SDK (it only ships
  `bot/v4/search`), so the startup identity lookup is a plain HTTP GET.
- Webhook decryption uses the SDK's own `AESCipher`, so the envelope format has
  a single implementation.
