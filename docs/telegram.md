# Telegram notifications

[Documentation index](README.md)

Each Guuey user can link one private Telegram account to a shared bot. In **Recurring
tasks → Telegram**, select **Link Telegram**, open the bot and press **Start**, then return
to Guuey and confirm the displayed account. Edit each desired task and enable **Send
matches to Telegram**. Notifications default to off; linking alone sends no research
results. Existing inbox updates are not backfilled.

## Configure the bot

For the server administrator:

1. Create a dedicated bot with [@BotFather](https://t.me/BotFather).
2. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in the server `.env`. Set
   `APP_PUBLIC_URL` to the Guuey URL reachable by your users, for links back to task chats.
   Keep the token out of frontend configuration and source control.
3. Run `docker compose --profile telegram up -d --build` to recreate the API and start
   the optional Telegram service. For local development, run `uv run python -m
   app.telegram_worker` from `backend/`, with the same bot configuration and
   database target as the API. For explicit environment loading, see [local setup](getting-started.md). Rebuild/restart the Telegram service after code changes.

The worker uses outbound [long polling](https://core.telegram.org/bots/api#getupdates),
so it needs access to the same application database and outbound HTTPS;
no public webhook or inbound Telegram port is needed. Use one dedicated bot, with no
other polling consumer or webhook. The worker verifies its bot identity and refuses an
existing webhook instead of removing it. Multiple Guuey Telegram workers sharing the
database elect one poller through a lease.

## Link ownership and expiry

The [link invitation](https://core.telegram.org/bots/features#deep-linking) expires in
ten minutes, is stored only as a hash, and can be used once. Only private chats are
accepted. Confirmation requires the same signed-in Guuey owner that created it; a
Telegram account cannot be linked to multiple owners. Normal users need their own
Guuey accounts: the shared development bearer token represents a single owner.

## Delivery outcomes

Each new matching inbox entry queues one delivery in the same transaction. The worker
sends a bounded plain-text excerpt, up to three source links and an Open in Guuey link
when `APP_PUBLIC_URL` is set. Failed or incomplete research does not create an alert.
Delivery uses [sendMessage](https://core.telegram.org/bots/api#sendmessage), with up to
five attempts for known transient failures and Telegram's `retry_after` for rate limits.
An ambiguous send timeout or interrupted send is marked unconfirmed and is not retried
blindly. The Telegram panel shows queued updates and the latest failed or unconfirmed
delivery; the full result remains in Guuey.

## Disconnect or change bots

**Disconnect Telegram** or `/stop` in the bot chat removes the link, cancels unsent
updates and disables Telegram for all that user's tasks. A message already in flight
may still arrive. Disabling Telegram on a task also cancels its unsent updates. Task
pause only stops future checks; an active check can still finish and notify. Reconnecting
requires enabling desired tasks again. Disabled Guuey accounts cannot receive deliveries.
Changing to a different bot requires users to link again; rotating the same bot's token
preserves links. Existing in-app research and notifications continue without Telegram.

## API

| Telegram endpoint | Behavior |
|---|---|
| `GET /api/telegram` | Read this owner's link, pending confirmation and delivery status |
| `POST /api/telegram/link` | Create a private, expiring bot link; replace any pending invitation |
| `POST /api/telegram/confirm` | Confirm the candidate using `{"attempt_id":"..."}` |
| `DELETE /api/telegram` | Disconnect or cancel a pending link; turn off task Telegram notifications |

`POST /api/tasks` and `PUT /api/tasks/{id}` accept `telegram_enabled` (default `false`).
Enabling it requires a confirmed link. These endpoints use the same owner authentication
and cookie request protection as the rest of the task API.

See [recurring tasks](recurring-tasks.md) for match conditions and schedule behavior.
