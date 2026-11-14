import { useEffect, useState } from "react";
import { getHealth, type Health, type ModelTrace } from "../chatApi";

type Section = "session" | "calls" | "usage";

export function DebugPanel({ onClose, chatId, traces }: { onClose: () => void; chatId: string; traces: ModelTrace[] }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Section>("session");

  useEffect(() => {
    let active = true;
    getHealth().then((value) => {
      if (active) setHealth(value);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "Health check failed");
    });
    return () => { active = false; };
  }, []);

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="debug-panel" role="dialog" aria-modal="true" aria-labelledby="debug-title">
        <header className="debug-header">
          <h2 id="debug-title">Debug</h2>
          <button className="quiet-button" type="button" onClick={onClose} aria-label="Close debug menu">Close</button>
        </header>
        <nav className="debug-tabs" aria-label="Debug sections">
          {([["session", "Session"], ["calls", "Model calls"], ["usage", "Usage"]] as const).map(([id, label]) => (
            <button key={id} type="button" className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="debug-body">
          {tab === "session" && <>
            <dl className="debug-details">
              <div><dt>Chat ID</dt><dd>{chatId || "—"}</dd></div>
              <div><dt>Backend</dt><dd>{health ? "Connected" : error ? "Unavailable" : "Checking…"}</dd></div>
              <div><dt>Provider</dt><dd>{health ? health.mode === "gemini" ? "Gemini" : "Local mock" : "—"}</dd></div>
              <div><dt>Model</dt><dd>{health?.model ?? "—"}</dd></div>
              <div><dt>API key auth</dt><dd>{health ? health.auth_configured ? "Configured" : "Missing" : "—"}</dd></div>
              <div><dt>Exa search</dt><dd>{health ? health.exa_configured ? "Configured" : "Not configured" : "—"}</dd></div>
              <div><dt>Version</dt><dd>{health?.version ?? "—"}</dd></div>
            </dl>
            {error && <p className="error-message" role="alert">{error}</p>}
            <p className="debug-note">Model traces are kept in this browser until disconnect or reload.</p>
          </>}
          {tab === "calls" && <>
            <h3>Model calls</h3>
            {traces.length === 0 ? <p className="debug-note">No model calls recorded for this chat in this browser session.</p> : traces.map((trace, index) => (
              <details className="debug-trace" key={index}>
                <summary>Turn {index + 1} · {trace.model} · {trace.intent}</summary>
                {trace.search_plan && <><h4>Search planner</h4><pre>{JSON.stringify(trace.search_plan, null, 2)}</pre></>}
                {trace.instructions && <><h4>Instructions</h4><pre>{trace.instructions}</pre></>}
                <h4>Model input</h4><pre>{trace.input}</pre>
                <h4>Model output</h4><pre>{trace.output}</pre>
              </details>
            ))}
          </>}
          {tab === "usage" && <>
            <h3>Token usage</h3>
            {traces.length === 0 ? <p className="debug-note">No usage recorded for this chat in this browser session.</p> : traces.map((trace, index) => (
              <div className="debug-trace" key={index}>
                <h4>Turn {index + 1} · {trace.model}</h4>
                <pre>{trace.usage ? JSON.stringify(trace.usage, null, 2) : "Not available"}</pre>
              </div>
            ))}
          </>}
        </div>
      </section>
    </div>
  );
}
