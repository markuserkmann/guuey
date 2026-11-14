import { useEffect, useState } from "react";
import {
  getHealth,
  runError,
  type Health,
  type ModelTrace,
  type ResearchRun,
} from "../chatApi";
import { Icon } from "./Icon";

type Section = "session" | "calls" | "usage";

export function DebugPanel({
  onClose,
  chatId,
  traces,
  run,
}: {
  run?: ResearchRun | null;
  onClose: () => void;
  chatId: string;
  traces: ModelTrace[];
}) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Section>("session");

  useEffect(() => {
    let active = true;
    getHealth()
      .then((value) => {
        if (active) setHealth(value);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(
            cause instanceof Error ? cause.message : "Health check failed",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <aside
      id="debug-panel"
      className="debug-panel"
      aria-labelledby="debug-title"
    >
      <header className="debug-header">
        <h2 id="debug-title">Debug</h2>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close debug menu"
        >
          <Icon name="close" />
        </button>
      </header>
      <nav className="debug-tabs" aria-label="Debug sections">
        {(
          [
            ["session", "Session"],
            ["calls", "Model calls"],
            ["usage", "Usage"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? "active" : ""}
            aria-current={tab === id ? "page" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="debug-body">
        {tab === "session" && (
          <>
            {run?.debug.error && (
              <div className="debug-trace">
                <p className="error-message">{runError(run)}</p>
                <pre>{JSON.stringify(run.debug.error, null, 2)}</pre>
              </div>
            )}
            <dl className="debug-details">
              <div>
                <dt>Run ID</dt>
                <dd>{run?.id ?? "No run"}</dd>
              </div>
              <div>
                <dt>Run status</dt>
                <dd>
                  {run ? `${run.mode} · ${run.status} · ${run.phase}` : "—"}
                </dd>
              </div>
              <div>
                <dt>Chat ID</dt>
                <dd>{chatId || "—"}</dd>
              </div>
              <div>
                <dt>Backend</dt>
                <dd>
                  {health ? "Connected" : error ? "Unavailable" : "Checking…"}
                </dd>
              </div>
              <div>
                <dt>Provider</dt>
                <dd>
                  {health
                    ? health.mode === "gemini"
                      ? "Gemini"
                      : "Local mock"
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{health?.model ?? "—"}</dd>
              </div>
              <div>
                <dt>API key auth</dt>
                <dd>
                  {health
                    ? health.auth_configured
                      ? "Configured"
                      : "Missing"
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Exa search</dt>
                <dd>
                  {health
                    ? health.exa_configured
                      ? "Configured"
                      : "Not configured"
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Version</dt>
                <dd>{health?.version ?? "—"}</dd>
              </div>
            </dl>
            {error && (
              <p className="error-message" role="alert">
                {error}
              </p>
            )}
            <p className="debug-note">
              Showing the latest run for this chat. Traces are saved with the
              run and update as it progresses.
            </p>
          </>
        )}
        {tab === "calls" && (
          <>
            <h3>Model calls</h3>
            {run && (
              <details className="debug-trace">
                <summary>Routing and run trace</summary>
                <pre>{JSON.stringify(run.debug, null, 2)}</pre>
              </details>
            )}
            {traces.length === 0 ? (
              <p className="debug-note">
                Model calls will appear here as this run progresses.
              </p>
            ) : (
              traces.map((trace, index) => (
                <details className="debug-trace" key={index}>
                  <summary>
                    Turn {index + 1} · {trace.model} · {trace.intent}
                  </summary>
                  {trace.search_plan && (
                    <>
                      <h4>Search planner</h4>
                      <pre>{JSON.stringify(trace.search_plan, null, 2)}</pre>
                    </>
                  )}
                  {trace.instructions && (
                    <>
                      <h4>Instructions</h4>
                      <pre>{trace.instructions}</pre>
                    </>
                  )}
                  <h4>Model input</h4>
                  <pre>{trace.input}</pre>
                  <h4>Model output</h4>
                  <pre>{trace.output}</pre>
                </details>
              ))
            )}
          </>
        )}
        {tab === "usage" && (
          <>
            <h3>Token usage</h3>
            {traces.length === 0 ? (
              <p className="debug-note">
                No token usage recorded for this run yet.
              </p>
            ) : (
              traces.map((trace, index) => (
                <div className="debug-trace" key={index}>
                  <h4>
                    Turn {index + 1} · {trace.model}
                  </h4>
                  <pre>
                    {trace.usage
                      ? JSON.stringify(trace.usage, null, 2)
                      : "Not available"}
                  </pre>
                </div>
              ))
            )}
          </>
        )}
      </div>
    </aside>
  );
}
