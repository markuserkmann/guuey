import type { ChatMessage, ResearchRun } from "./chatApi";
import { runProgress } from "./chatApi";

export function isRunActive(run: Pick<ResearchRun, "status">) {
  return run.status === "queued" || run.status === "running";
}

export function runMessages(
  messages: ChatMessage[],
  run: ResearchRun,
): ChatMessage[] {
  const items = [...messages];
  const userId = `user:${run.request_id}`;
  if (!run.parent_id && !items.some((item) => item.id === userId))
    items.push({
      id: userId,
      role: "user",
      content: run.message,
      attachments: run.attachments,
      delivery: "sent",
    });

  const id = `assistant:${run.id}`;
  const index = items.findIndex((item) => item.id === id);
  const saved = index >= 0 ? items[index] : undefined;
  const active = isRunActive(run);
  const live =
    active && run.live_output?.kind !== "progress" ? run.live_output : null;
  const answer: ChatMessage = {
    ...saved,
    id,
    role: "assistant",
    content: live?.text || run.result?.reply || saved?.content || "",
    sources: live?.text ? [] : (run.result?.sources ?? saved?.sources),
    delivery: active ? "updating" : undefined,
    draft: active && live?.kind === "draft",
  };
  if (index >= 0) items[index] = answer;
  else items.push(answer);
  return items;
}

export function runPresentation(run: ResearchRun, reconnecting = false) {
  const active = isRunActive(run);
  const hasAnswer =
    !!run.result?.reply ||
    (!!run.live_output?.text && run.live_output.kind !== "progress");
  if (!active) {
    if (
      run.status === "partial" &&
      run.result?.next_action?.kind === "browser_opt_in"
    )
      return {
        title: "Browser check available",
        detail:
          "Checking websites will take longer. It starts only if you choose it.",
        state: "incomplete",
      } as const;
    if (run.phase === "submission_unknown")
      return {
        title: "Browser result needs review",
        detail:
          "Work has stopped. Check your account before trying this action again.",
        state: "incomplete",
      } as const;
    const terminal = {
      completed: {
        title: "Answer complete",
        detail: "Finished updating this answer.",
        state: "complete",
      },
      partial: {
        title: "Finished with gaps",
        detail: "Work has stopped. Some information could not be confirmed.",
        state: "incomplete",
      },
      cancelled: {
        title: "Stopped",
        detail: "Work was cancelled. Any answer shown may be incomplete.",
        state: "incomplete",
      },
      failed: {
        title: "Could not finish",
        detail: "Work has stopped. Any answer shown may be incomplete.",
        state: "incomplete",
      },
    } as const;
    return terminal[run.status as keyof typeof terminal];
  }
  if (reconnecting)
    return {
      title: "Reconnecting to live updates…",
      detail:
        "The connection was interrupted. Checking the saved run for its latest status.",
      state: "reconnecting",
    } as const;
  if (
    run.phase.startsWith("awaiting_") ||
    run.browser?.request?.status === "pending"
  )
    return {
      title: "Waiting for you",
      detail: "Work is paused. Respond below to continue.",
      state: "waiting",
    } as const;
  if (run.phase === "browser_starting")
    return {
      title: hasAnswer ? "Switching to browser…" : "Opening the browser…",
      detail: hasAnswer
        ? "Still working. Your answer will update here as I check the websites."
        : "I’m opening websites to find the information for your answer.",
      state: "working",
    } as const;
  if (run.phase === "browser_working" || run.phase === "browser_verifying")
    return {
      title:
        run.phase === "browser_verifying"
          ? "Checking the browser result…"
          : "Working in the browser…",
      detail: hasAnswer
        ? "Still updating your answer with information from the websites."
        : "Still working. The answer will appear here when it’s ready.",
      state: "working",
    } as const;
  return {
    title: runProgress(run),
    detail: hasAnswer
      ? "Still working. This answer may change as I check and improve it."
      : "I’ll update this response as information comes in.",
    state: "working",
  } as const;
}
