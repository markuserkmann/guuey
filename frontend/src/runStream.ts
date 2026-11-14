import { DEV_SESSION, tokenError } from "./auth";
import type { ResearchRun } from "./chatApi";

export type RunSnapshot = Pick<
  ResearchRun,
  "id" | "status" | "phase" | "result" | "live_output" | "browser"
>;

export async function streamRun(
  token: string,
  runId: string,
  signal: AbortSignal,
  onSnapshot: (snapshot: RunSnapshot) => void,
): Promise<void> {
  const invalid = token === DEV_SESSION ? null : tokenError(token);
  if (invalid) throw new Error(invalid);
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/events`,
    {
      signal,
      headers:
        token === DEV_SESSION ? {} : { Authorization: `Bearer ${token}` },
    },
  );
  if (!response.ok || !response.body)
    throw new Error(`Could not connect to live updates (${response.status}).`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      pending += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = pending.indexOf("\n\n")) >= 0) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        if (!frame.split("\n").includes("event: run")) continue;
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice(6))
          .join("\n");
        const snapshot = JSON.parse(data) as RunSnapshot;
        if (snapshot.id === runId) onSnapshot(snapshot);
      }
      if (pending.length > 2_000_000)
        throw new Error("Live update exceeded the response limit.");
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
