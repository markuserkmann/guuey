import type { ResearchRun } from "../chatApi";

export function BrowserChoice({
  run,
  activeChatId,
  busy,
  enabled,
  onChoose,
}: {
  run: ResearchRun | null;
  activeChatId: string;
  busy: boolean;
  enabled: boolean;
  onChoose: (choice: { content: string; forceBrowser: boolean }) => void;
}) {
  if (
    !run ||
    run.chat_id !== activeChatId ||
    run.status !== "partial" ||
    run.result?.next_action?.kind !== "browser_opt_in" ||
    busy ||
    !enabled
  )
    return null;

  return (
    <div className="waiting" role="group" aria-label="Website checking choice">
      <button
        className="quiet-button"
        type="button"
        onClick={() =>
          onChoose({
            content: "Yes, use the browser to finish the previous search.",
            forceBrowser: true,
          })
        }
      >
        Yes, use browser
      </button>
      <button
        className="quiet-button"
        type="button"
        onClick={() =>
          onChoose({ content: "Keep web results", forceBrowser: false })
        }
      >
        Keep web results
      </button>
    </div>
  );
}
