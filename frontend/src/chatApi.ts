export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export type Health = {
  status: "ok";
  mode: "mock" | "gemini";
  version: string;
  model: string;
};

export async function getHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  return response.json() as Promise<Health>;
}

export async function sendMessage(content: string, history: ChatMessage[]): Promise<ChatMessage> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: content,
      history: history.slice(-20).map(({ role, content: text }) => ({ role, content: text })),
    }),
  });
  if (!response.ok) throw new Error(`Message request failed (${response.status}). Check the Debug panel.`);
  const data = (await response.json()) as { reply: string };
  return { id: crypto.randomUUID(), role: "assistant", content: data.reply };
}
