import { useEffect, useState } from "react";
import { getHealth, type Health } from "../chatApi";

export function DebugPanel({ onClose }: { onClose: () => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");

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
        <dl className="debug-details">
          <div><dt>Backend</dt><dd>{health ? "Connected" : error ? "Unavailable" : "Checking…"}</dd></div>
          <div><dt>Provider</dt><dd>{health?.mode === "gemini" ? "Gemini" : "Local mock"}</dd></div>
          <div><dt>Model</dt><dd>{health?.model ?? "—"}</dd></div>
          <div><dt>API key auth</dt><dd>{health ? health.auth_configured ? "Configured" : "Missing" : "—"}</dd></div>
          <div><dt>Version</dt><dd>{health?.version ?? "—"}</dd></div>
        </dl>
        {error && <p className="error-message" role="alert">{error}</p>}
        <p className="debug-note">Replies use the local test backend. No model provider is called.</p>
      </section>
    </div>
  );
}
