import { liveViewUrl, siteLabel, type BrowserState } from "../browserApi";

export function BrowserActivity({
  state,
  onReview,
}: {
  state: BrowserState;
  onReview: () => void;
}) {
  const view = liveViewUrl(state.live_view_url);
  const preview = view ? new URL(view) : null;
  if (preview) {
    preview.searchParams.set("view_only", "true");
    preview.searchParams.set("scale", "true");
    preview.hash = "";
  }
  const latest = state.steps.at(-1);
  return (
    <section className="browser-activity" aria-label="Browser activity">
      <div className="browser-activity-heading">
        <details>
          <summary>
            {latest ? latest.label : "Browser activity"}
            {latest?.url && <span>{siteLabel(latest.url)}</span>}
          </summary>
          {state.steps.length ? (
            <ol className="browser-steps">
              {state.steps.map((step) => (
                <li key={step.seq}>
                  <span>
                    {step.label}
                    <small>{siteLabel(step.url)}</small>
                  </span>
                  <span
                    className={
                      step.status === "error"
                        ? "error-message"
                        : "browser-step-status"
                    }
                  >
                    {step.status === "ok"
                      ? "Done"
                      : step.status === "waiting"
                        ? "Waiting"
                        : "Needs attention"}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p>The browser is connecting.</p>
          )}
        </details>
        {view && (
          <a
            className="quiet-button"
            href={view}
            target="_blank"
            rel="noopener noreferrer"
            referrerPolicy="no-referrer"
          >
            Open live view
          </a>
        )}
      </div>
      {preview && (
        <figure className="browser-preview">
          <iframe
            src={preview.href}
            title="Live browser preview (read-only)"
            width="1280"
            height="720"
            sandbox="allow-scripts allow-same-origin"
            referrerPolicy="no-referrer"
          />
          <figcaption>
            Read-only preview. Use Open live view to interact or if the preview
            does not connect.
          </figcaption>
        </figure>
      )}
      {state.actions.map((action) => (
        <div className="browser-action-result" key={action.id}>
          <p>{action.summary}</p>
          <p
            className={
              action.status === "unverified"
                ? "browser-warning"
                : "browser-note"
            }
          >
            {
              {
                attempting: "Waiting for approval",
                approved: "Approved",
                submitted: "Submitted once. Checking the result…",
                verified: "Confirmed",
                unverified:
                  "Result uncertain. Check your account before trying again.",
                declined: "Declined",
                expired: "Approval expired",
                not_completed: "Marked not completed by you",
              }[action.status]
            }
          </p>
          {action.status === "unverified" && (
            <button className="quiet-button" onClick={onReview}>
              Review uncertain actions
            </button>
          )}
        </div>
      ))}
    </section>
  );
}
