import { DEV_SESSION, tokenError } from "./auth";
import type { TextAttachment } from "./attachments";
import type { BrowserState } from "./browserApi";

export type ChatSource = { title: string; url: string };
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  attachments?: TextAttachment[];
  delivery?: "sending" | "sent" | "failed" | "updating";
  draft?: boolean;
};
export type ChatSummary = {
  id: string;
  title: string;
  updated_at: string;
  pinned?: boolean;
};
export type ChatDetail = ChatSummary & { messages: ChatMessage[] };
export type ModelTrace = {
  search_plan?: {
    provider: string;
    model: string;
    instructions: string;
    input: string;
    output: unknown;
    usage: unknown;
  } | null;
  provider: string;
  model: string;
  intent: string;
  instructions?: string;
  input: string;
  output: string;
  usage: Record<string, unknown> | null;
};
export type Health = {
  status: "ok";
  mode: "mock" | "gemini";
  version: string;
  model: string;
  auth_configured: boolean;
  exa_configured: boolean;
  browser_enabled: boolean;
  saved_logins_enabled: boolean;
};

export const verifySession = (token: string) =>
  request<{ authenticated: boolean }>("/api/session", token);

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function apiFetch(
  path: string,
  token: string,
  init: RequestInit = {},
  sensitive = false,
): Promise<Response> {
  const validationError = token === DEV_SESSION ? null : tokenError(token);
  if (validationError) throw new Error(validationError);
  const response = await fetch(path, {
    cache: "no-store",
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token === DEV_SESSION ? {} : { Authorization: `Bearer ${token}` }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    if (sensitive) {
      const messages: Record<number, string> = {
        401: "Your connection is no longer authorized. Reconnect to continue.",
        404: "This browser request or saved login is no longer available.",
        409: "The browser state changed. Refresh before trying again. A login in use cannot be replaced.",
        422: "Check the site URL and cookie format. The server could not accept this input.",
        503: "Saved logins and private input are unavailable. Check the backend session-key configuration.",
      };
      throw new ApiError(
        messages[response.status] ||
          "The browser request failed. Try again after checking its status.",
        response.status,
      );
    }
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : Array.isArray(body?.detail)
          ? body.detail.map((item) => item.msg).join("; ")
          : "";
    throw new ApiError(
      detail || `Request failed (${response.status}).`,
      response.status,
    );
  }
  return response;
}

async function request<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(path, token, init);
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export async function getHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  return response.json() as Promise<Health>;
}

export const listChats = (token: string, query = "") =>
  request<ChatSummary[]>(
    `/api/chats${query ? `?query=${encodeURIComponent(query)}` : ""}`,
    token,
  );

export const createChat = (token: string) =>
  request<ChatSummary>("/api/chats", token, { method: "POST" });

export const pinChat = (token: string, id: string, pinned: boolean) =>
  request<ChatSummary>(`/api/chats/${encodeURIComponent(id)}`, token, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });

export const loadChat = (token: string, id: string) =>
  request<ChatDetail>(`/api/chats/${encodeURIComponent(id)}`, token);

export const sendMessage = (
  token: string,
  chatId: string,
  content: string,
  search: boolean,
) =>
  request<{ user: ChatMessage; assistant: ChatMessage; debug: ModelTrace }>(
    `/api/chats/${encodeURIComponent(chatId)}/messages`,
    token,
    { method: "POST", body: JSON.stringify({ message: content, search }) },
  );

export type ResearchRun = {
  id: string;
  request_id: string;
  chat_id: string;
  mode: "auto" | "fast" | "deep";
  message: string;
  attachments?: TextAttachment[];
  parent_id?: string | null;
  status:
    "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
  phase: string;
  browser?: BrowserState | null;
  live_output?: { kind: "answer" | "draft" | "progress"; text: string } | null;
  result: { reply: string; sources: ChatSource[] } | null;
  debug: {
    stages?: ModelTrace[];
    decision?: { deep_recommended: boolean; workflow?: string };
    error_type?: string;
    error?: {
      code: string;
      message: string;
      retryable: boolean;
      retry_after: number | null;
      detail?: string;
      status_code?: number | null;
    };
    operation?: {
      stage: string;
      status: string;
      attempt: number;
      retry_at?: string | null;
      delay_seconds?: number | null;
    };
  };
};
export const startRun = (
  token: string,
  chatId: string,
  message: string,
  mode: ResearchRun["mode"],
  requestId: string,
  attachments: TextAttachment[] = [],
) =>
  request<ResearchRun>(`/api/chats/${encodeURIComponent(chatId)}/runs`, token, {
    method: "POST",
    body: JSON.stringify({
      message,
      mode,
      request_id: requestId,
      attachments,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    }),
  });
export const getRun = (token: string, id: string) =>
  request<ResearchRun>(`/api/runs/${encodeURIComponent(id)}`, token);
export const latestRun = (token: string, chatId: string) =>
  request<ResearchRun | null>(
    `/api/chats/${encodeURIComponent(chatId)}/runs/latest`,
    token,
  );
export const continueRun = (token: string, id: string) =>
  request<ResearchRun>(`/api/runs/${encodeURIComponent(id)}/continue`, token, {
    method: "POST",
  });
export const cancelRun = (token: string, id: string) =>
  request<ResearchRun>(`/api/runs/${encodeURIComponent(id)}/cancel`, token, {
    method: "POST",
  });

export function runError(run: ResearchRun): string {
  if (run.debug.error?.status_code === 402)
    return "Gemini prepaid credits are depleted. Add credits in Google AI Studio, then try again.";
  if (run.debug.error) return run.debug.error.message;
  if (run.debug.error_type === "ModelRateLimitError")
    return "Gemini temporarily rate-limited this request. Wait a moment and try again.";
  if (run.debug.error_type === "ModelQuotaExceededError")
    return "Gemini quota is exhausted. Check the project's usage and billing in Google AI Studio.";
  return run.status === "failed"
    ? `Research stopped: ${run.phase.replaceAll("_", " ")}.`
    : "";
}

export function runProgress(run: ResearchRun | null): string {
  if (run?.phase === "waiting_for_provider")
    return `Gemini is temporarily busy. Retrying ${run.debug.operation?.stage ?? "request"} shortly…`;
  const labels: Record<string, string> = {
    queued: "Waiting to start…",
    routing: "Reading your request…",
    planning: "Planning searches…",
    retrieval: "Reading sources…",
    answering: "Writing…",
    verifying: "Checking the evidence…",
    answer_ready: "Answer ready. Checking remaining gaps…",
    browser_starting: "Opening the browser…",
    browser_working: "Working in the browser…",
    awaiting_input: "Waiting for your answer",
    awaiting_approval: "Waiting for your approval",
    awaiting_takeover: "Your turn in the live view",
    browser_verifying: "Checking the result…",
  };
  return (
    (run && labels[run.phase]) || run?.phase.replaceAll("_", " ") || "Starting…"
  );
}
