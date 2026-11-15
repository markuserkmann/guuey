import { authHeaders, sessionExpired } from "./auth";
import type { ResearchRun } from "./chatApi";
import { isRunActive } from "./runPresentation";

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
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/events`,
    {
      signal,
      credentials: "same-origin",
      headers: authHeaders(token),
    },
  );
  if (!response.ok || !response.body) {
    sessionExpired(token, response.status);
    throw new Error(`Could not connect to live updates (${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let event = "";
  let data: string[] = [];
  let frameSize = 0;

  function consumeLine(line: string): boolean {
    if (!line) {
      const eventData = data.join("\n");
      const isRun = event === "run";
      event = "";
      data = [];
      frameSize = 0;
      if (!isRun || !eventData) return false;
      const snapshot = JSON.parse(eventData) as RunSnapshot;
      if (!snapshot || snapshot.id !== runId) return false;
      if (
        ![
          "queued",
          "running",
          "completed",
          "partial",
          "failed",
          "cancelled",
        ].includes(snapshot.status) ||
        typeof snapshot.phase !== "string" ||
        (snapshot.live_output != null &&
          (typeof snapshot.live_output.text !== "string" ||
            !["answer", "draft", "progress"].includes(
              snapshot.live_output.kind,
            ))) ||
        (snapshot.result != null &&
          (typeof snapshot.result.reply !== "string" ||
            !Array.isArray(snapshot.result.sources)))
      )
        throw new Error("Invalid live update. Reconnecting to the saved run.");
      onSnapshot(snapshot);
      return !isRunActive(snapshot);
    }
    frameSize += line.length;
    if (frameSize > 2_000_000)
      throw new Error("Live update exceeded the response limit.");
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    if (field === "data") data.push(value);
    return false;
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = pending.search(/[\r\n]/)) >= 0) {
        // A CRLF pair can arrive in separate network chunks.
        if (
          !done &&
          pending[boundary] === "\r" &&
          boundary === pending.length - 1
        )
          break;
        const line = pending.slice(0, boundary);
        const separator =
          pending[boundary] === "\r" && pending[boundary + 1] === "\n" ? 2 : 1;
        pending = pending.slice(boundary + separator);
        if (consumeLine(line)) return;
      }
      if (pending.length + frameSize > 2_000_000)
        throw new Error("Live update exceeded the response limit.");
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
