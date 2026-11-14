export type ChatSource = { title: string; url: string };
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
};
export type ChatSummary = { id: string; title: string; updated_at: string };
export type ChatDetail = ChatSummary & { messages: ChatMessage[] };
export type ModelTrace = { search_plan?: { provider: string; model: string; instructions: string; input: string; output: unknown; usage: unknown } | null; provider: string; model: string; intent: string; instructions?: string; input: string; output: string; usage: Record<string, unknown> | null };
export type Health = {
  status: "ok";
  mode: "mock" | "gemini";
  version: string;
  model: string;
  auth_configured: boolean;
  exa_configured: boolean;
};

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export async function getHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  return response.json() as Promise<Health>;
}

export const listChats = (token: string, query = "") =>
  request<ChatSummary[]>(`/api/chats${query ? `?query=${encodeURIComponent(query)}` : ""}`, token);

export const createChat = (token: string) =>
  request<ChatSummary>("/api/chats", token, { method: "POST" });

export const loadChat = (token: string, id: string) =>
  request<ChatDetail>(`/api/chats/${encodeURIComponent(id)}`, token);

export const sendMessage = (token: string, chatId: string, content: string, search: boolean) =>
  request<{ user: ChatMessage; assistant: ChatMessage; debug: ModelTrace }>(
    `/api/chats/${encodeURIComponent(chatId)}/messages`, token,
    { method: "POST", body: JSON.stringify({ message: content, search }) },
  );
