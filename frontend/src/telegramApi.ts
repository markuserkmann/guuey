import { apiFetch } from "./chatApi";

export type TelegramIdentity = {
  telegram_id: string;
  username: string | null;
  first_name: string;
};

export type TelegramStatus = {
  configured: boolean;
  bot_username: string | null;
  worker_online: boolean;
  linked: TelegramIdentity | null;
  pending: {
    id: string;
    expires_at: string;
    candidate: TelegramIdentity | null;
  } | null;
  queued_count: number;
  delivery_issue: { status: string; error: string; title: string } | null;
};

export async function telegramStatus(
  token: string,
  signal?: AbortSignal,
): Promise<TelegramStatus> {
  return (await apiFetch("/api/telegram", token, { signal })).json();
}

export async function linkTelegram(
  token: string,
): Promise<TelegramStatus & { url: string }> {
  return (
    await apiFetch("/api/telegram/link", token, { method: "POST" })
  ).json();
}

export async function confirmTelegram(
  token: string,
  attemptId: string,
): Promise<TelegramStatus> {
  return (
    await apiFetch("/api/telegram/confirm", token, {
      method: "POST",
      body: JSON.stringify({ attempt_id: attemptId }),
    })
  ).json();
}

export async function disconnectTelegram(token: string) {
  await apiFetch("/api/telegram", token, { method: "DELETE" });
}
