# Feishu / Lark bot setup

Self-hosted Feishu (Lark) bot for OpenSRE incident investigations. It receives
group @mentions and direct messages, streams investigation progress into a card
that it edits in place, collects clarifying answers through an interactive card,
and posts the final report with a **View in OpenSRE** link.

The bot shares its pipeline with the Microsoft Teams bot through the
`im-core/` package, so behaviour and rendering are intentionally close to
[`teams-bot`](../teams-bot/README.md).

## How it talks to OpenSRE

```
Feishu  ──(event)──▶  feishu-bot  ──(SSE /investigate)──▶  sre-agent
                          │  ▲                                  │
                          │  └──(progress, question card)───────┘
                          └──(POST /answer, /threads/{id}/queue-message)
```

`feishu-bot` is a thin adapter: all SSE interpretation, progress rendering,
markdown parsing, and the timeout/throttle policy live in `im-core/`, and
`feishu-bot` only supplies the Feishu rendering and message identity through the
core's `PlatformHooks` protocol.

## 1. Create the app

1. Open the [Feishu Open Platform](https://open.feishu.cn/app) and create a
   **self-built application** (自建应用).
2. From **Credentials & Basic Info**, note the **App ID** (`cli_…`) and
   **App Secret**.

## 2. Grant permissions

Under **Permissions & Scopes**, enable at least:

| Scope | Why |
|---|---|
| `im:message` | receive message events |
| `im:message:send_as_bot` | send and update messages as the bot |
| `im:chat:readonly` | look up the bot's group memberships (optional but useful) |
| `contact:user.base:readonly` | resolve a sender's display name for `trigger_actor` (optional) |

Without `contact:user.base:readonly` the bot simply leaves `trigger_actor` empty
for that message — by design it never stores a raw `open_id` as a person's name.

## 3. Subscribe to events and callbacks

Under **Events & Callbacks**:

- **Event**: `im.message.receive_v1` (message received)
- **Callback**: `card.action.trigger` (interactive-card button / form submit)

Two delivery modes are supported. Pick one with `FEISHU_MODE`.

### `FEISHU_MODE=ws` (default) — long connection

The bot opens an outbound WebSocket, so **no public URL and no tunnel are
required**. This is the right choice for local development and for self-hosted
installs that do not expose the bot.

The long-connection mode uses the same app credentials; the verification token
and encrypt key are not used on this path.

### `FEISHU_MODE=webhook` — request URL

Expose the bot and point Feishu at it:

| Endpoint | Purpose |
|---|---|
| `POST /webhook/event` | message events |
| `POST /webhook/card` | card callbacks |

Set the request URL in the console to `https://<your-host>/webhook/event`.

When the corresponding secrets are configured, the bot **requires** them:
`FEISHU_VERIFICATION_TOKEN` is compared on every request, and if
`FEISHU_ENCRYPT_KEY` is set the payload must decrypt. This is deliberate — an
unauthenticated webhook would let anyone inject messages. The `challenge`
handshake is echoed as-is.

For local development, expose the port with a tunnel:

```bash
cloudflared tunnel --protocol http2 --url http://localhost:3979
# or: ngrok http 3979
```

## 4. Configure the environment

Add these to the repo-root `.env` (see [`feishu-bot/env.example`](../feishu-bot/env.example)):

| Variable | Description |
|---|---|
| `FEISHU_APP_ID` | App ID (`cli_…`) |
| `FEISHU_APP_SECRET` | App Secret |
| `FEISHU_MODE` | `ws` (default) or `webhook` |
| `FEISHU_VERIFICATION_TOKEN` | webhook mode only — verified on every request |
| `FEISHU_ENCRYPT_KEY` | webhook mode only — payload decryption |
| `FEISHU_BOT_NAME` | display name used in help text (default `OpenSRE`) |
| `PORT` | HTTP listen port (default `3979`) |
| `SRE_AGENT_URL` | sre-agent base URL (compose sets `http://sre-agent:8000`) |
| `INVESTIGATE_AUTH_TOKEN` | bearer token for `/investigate` and `/answer` |
| `WEB_UI_PUBLIC_BASE_URL` | console origin used for the **View in OpenSRE** link |
| `OPENSRE_TEAM_TOKEN` | optional — sent as `X-OpenSRE-Team-Token` so runs are attributed to a real org/team instead of `local`/`default` |

If `FEISHU_APP_ID` or `FEISHU_APP_SECRET` is missing, `app.py` logs a warning and
exits with code 0 rather than crash-looping, matching the Teams bot.

## 5. Run it

```bash
make dev          # core stack
make dev-feishu   # opt in to the Feishu bot
```

Or directly:

```bash
docker compose --profile feishu up -d --build feishu-bot
```

Check the logs for the startup line. The bot must be **added to a group** to see
group messages: open the group, add the bot as a member, then `@` it.

## Behaviour

- **Group chat:** the bot only responds when @mentioned; the mention placeholder
  is stripped before the prompt is sent.
- **Direct message:** no mention needed; `help` or `status` returns a welcome card.
- **Threading:** the conversation id (`chat_id`) is hashed into the investigation
  `thread_id`, so replying again in the same chat continues the same
  investigation.
- **Live progress:** one progress card is edited in place (Feishu card `PATCH`),
  throttled more slowly than Teams because Feishu rate-limits card updates.
- **Questions:** the agent's clarifying questions render as a form card
  (single/multi-select plus an optional comment). Submitting posts to
  `/answer`. Note that option descriptions are shown here even though the Teams
  card drops them.
- **Timeouts:** when a question times out, the same card is replaced with a
  timeout notice instead of a second card appearing.
- **Mid-run messages:** a message sent while an investigation is running is
  queued through `/threads/{id}/queue-message` and consumed by the run, exactly
  as in Teams.
- **Final reply:** the last result plus a **View in OpenSRE** link.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Bot logs a warning and exits immediately | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` not set |
| Bot ignores group messages | it was not @mentioned, or it is not a member of the group |
| `code: 99991672` on an OpenAPI call | the app lacks that scope; add it and republish the app version |
| Card button does nothing | the `card.action.trigger` callback is not subscribed, or (webhook mode) the verification token / encrypt key does not match |
| Runs show as `web_ui` in the console | `"feishu"` is missing from sre-agent's trigger-source allowlist — see the note in `sre-agent/server_simple.py` |
| No `View in OpenSRE` link | `WEB_UI_PUBLIC_BASE_URL` is empty, or the run has no `run_id` yet |

## Tests

```bash
sh scripts/im-test.sh check feishu-bot   # ruff + pytest, inside the dev image
```

The suite covers message parsing and @mention detection, card building and
conversion, the card-action answer round-trip, and the credential-missing exit
path. Group-chat behaviour is exercised with synthetic events because the test
tenant's bot is not a member of any group.
