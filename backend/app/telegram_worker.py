"""Run with python -m app.telegram_worker; uses outbound Telegram long polling."""

import asyncio
import logging
import re
from uuid import uuid4

import httpx

from app.runs import RunStore
from app.telegram import TelegramSettings, TelegramStore, notification_text

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, code=0, retry_after=0, uncertain=False):
        super().__init__("Telegram request failed")
        self.code = code
        self.retry_after = retry_after
        self.uncertain = uncertain


class TelegramClient:
    def __init__(self, client, settings):
        self.client = client
        self.base_url = f"https://api.telegram.org/bot{settings.token}/"

    async def call(self, method, payload=None):
        try:
            response = await self.client.post(self.base_url + method, json=payload or {})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TelegramError(retry_after=15) from None
        except httpx.HTTPError:
            raise TelegramError(uncertain=True) from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError(uncertain=True) from None
        if not isinstance(data, dict):
            raise TelegramError(uncertain=True) from None
        if data.get("ok") is True and response.is_success and "result" in data:
            return data["result"]
        code = data.get("error_code", response.status_code)
        parameters = data.get("parameters") or {}
        retry_after = parameters.get("retry_after", 0) if isinstance(parameters, dict) else 0
        raise TelegramError(code=code if isinstance(code, int) else 0,
                            retry_after=max(retry_after, 0) if isinstance(retry_after, int) else 0,
                            uncertain=data.get("ok") is not False)

    async def send(self, chat_id, text):
        result = await self.call("sendMessage", {"chat_id": chat_id, "text": text,
                                                "link_preview_options": {"is_disabled": True}})
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramError(uncertain=True)
        return result["message_id"]


async def handle_update(store, client, update):
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat, sender = message.get("chat") or {}, message.get("from") or {}
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return
    chat_id = chat.get("id")
    if (chat.get("type") != "private" or type(chat_id) is not int or not 0 < chat_id < 2**52
            or sender.get("id") != chat_id or sender.get("is_bot") is not False):
        return
    text = message.get("text", "")
    if not isinstance(text, str):
        return
    parts = text.split(maxsplit=1)
    if not parts:
        return
    command = parts[0].split("@", 1)[0]
    if command == "/stop":
        store.stop_chat(chat_id)
        reply = "Telegram disconnected. Future task updates will stay in Guuey."
    elif command == "/start" and len(parts) == 2:
        username = sender.get("username")
        username = username if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) else None
        first_name = sender.get("first_name")
        first_name = first_name[:128] if isinstance(first_name, str) else "Telegram user"
        accepted = store.receive_link(parts[1], chat_id, username, first_name)
        reply = ("Return to Guuey → Recurring tasks → Telegram and confirm this account. "
                 "Then choose which tasks can send updates here." if accepted else
                 "This link is no longer available. Check your pending link in Guuey, or create a new one.")
    elif command in {"/start", "/help"}:
        reply = "To link your account, open Guuey → Recurring tasks → Telegram. Send /stop here to disconnect."
    else:
        return
    try:
        await client.send(chat_id, reply)
    except TelegramError:
        # Guuey displays the durable linking state even if the bot reply fails.
        pass


async def deliver_one(store, client):
    delivery = store.claim_delivery()
    if not delivery:
        return False
    try:
        message_id = await client.send(delivery["chat_id"], notification_text(delivery, store.settings))
    except TelegramError as exc:
        if exc.uncertain:
            store.finish_delivery(delivery, "unknown", error="Delivery could not be confirmed. Read the update in Guuey.")
        elif (exc.code == 429 or exc.code >= 500 or exc.code == 0) and delivery["attempts"] < 5:
            store.finish_delivery(delivery, "pending", delay=max(exc.retry_after, 15 * 2 ** delivery["attempts"]),
                                  error="Telegram is temporarily unavailable. Delivery will be retried.")
        else:
            error = ("Telegram could not reach your chat. Unblock the bot or reconnect your account."
                     if exc.code in {400, 403} else "Telegram delivery failed. Read the update in Guuey.")
            store.finish_delivery(delivery, "failed", error=error)
    else:
        store.finish_delivery(delivery, "sent", message_id=message_id)
    return True


async def run():
    settings = TelegramSettings.from_env()
    if not settings.configured:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME before starting the Telegram worker.")
    store, worker_id = TelegramStore(RunStore(), settings), str(uuid4())
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as http:
        client = TelegramClient(http, settings)
        try:
            identity = await client.call("getMe")
            if (not isinstance(identity, dict) or str(identity.get("id")) != settings.bot_id
                    or identity.get("username", "").casefold() != settings.username.casefold()):
                raise SystemExit("TELEGRAM_BOT_USERNAME does not match the configured bot.")
            webhook = await client.call("getWebhookInfo")
            if not isinstance(webhook, dict) or webhook.get("url"):
                raise SystemExit("This bot has a webhook. Use a dedicated bot with no webhook for Guuey.")
        except TelegramError:
            raise SystemExit("Could not verify the Telegram bot. Check its credentials and network connection.") from None
        log.info("Telegram worker started")
        try:
            while True:
                if not store.lease(worker_id):
                    await asyncio.sleep(5)
                    continue
                for _ in range(5):
                    if not store.lease(worker_id) or not await deliver_one(store, client):
                        break
                if not store.lease(worker_id):
                    continue
                try:
                    updates = await client.call("getUpdates", {"offset": store.offset(), "timeout": 10,
                                                                "limit": 20, "allowed_updates": ["message"]})
                    if not isinstance(updates, list):
                        raise TelegramError(uncertain=True)
                    for update in updates:
                        if not store.lease(worker_id):
                            break
                        if isinstance(update, dict) and type(update.get("update_id")) is int:
                            await handle_update(store, client, update)
                            store.offset(update["update_id"] + 1)
                except TelegramError as exc:
                    if exc.code in {401, 409}:
                        raise SystemExit("Telegram polling rejected. Check the bot token and stop any other poller or webhook.") from None
                    log.warning("Telegram polling unavailable; retrying")
                    await asyncio.sleep(max(5, exc.retry_after))
        finally:
            store.release(worker_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Bot API credentials are part of the URL; never include HTTP request URLs in logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
