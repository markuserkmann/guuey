import { useState, type ReactNode } from "react";
import type { ResearchRun } from "../chatApi";
import { isRunActive, runPresentation } from "../runPresentation";
import { Icon } from "./Icon";

export function RunActivity({
  run,
  reconnecting,
  onStop,
  children,
}: {
  run: ResearchRun;
  reconnecting: boolean;
  onStop: () => Promise<void>;
  children?: ReactNode;
}) {
  const active = isRunActive(run);
  const status = runPresentation(run, reconnecting);
  const [stopping, setStopping] = useState(false);

  return (
    <>
      <section
        className={`run-activity run-activity-${status.state}`}
        aria-label="Response status"
      >
        <div className="run-status-heading">
          <div
            className="run-status-copy"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <span className="run-status-symbol" aria-hidden="true">
              {status.state === "working" || status.state === "reconnecting" ? (
                <span className="activity-spinner" />
              ) : (
                <Icon
                  name={
                    status.state === "complete"
                      ? "check"
                      : status.state === "waiting"
                        ? "pause"
                        : "stop"
                  }
                />
              )}
            </span>
            <div>
              <p className="run-status-title">{status.title}</p>
              <p className="run-status-description">{status.detail}</p>
            </div>
          </div>
          {active && (
            <div className="run-status-controls">
              <button
                className="quiet-button"
                type="button"
                disabled={stopping}
                onClick={async () => {
                  setStopping(true);
                  try {
                    await onStop();
                  } finally {
                    setStopping(false);
                  }
                }}
              >
                {stopping ? "Stopping…" : "Stop"}
              </button>
            </div>
          )}
        </div>
      </section>
      {children && <div className="run-activity-details">{children}</div>}
    </>
  );
}
