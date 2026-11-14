import { useState, type FormEvent } from "react";
import { tokenError } from "../auth";
import { verifySession } from "../chatApi";

export function ConnectPanel({
  onConnect,
}: {
  onConnect: (token: string) => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (connecting) return;
    const token = value.trim();
    const validationError = tokenError(token);
    if (validationError) {
      setError(validationError);
      return;
    }
    setConnecting(true);
    setError("");
    try {
      await verifySession(token);
      onConnect(token);
      setValue("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not authenticate. Try again.",
      );
    } finally {
      setConnecting(false);
    }
  }

  return (
    <main className="connect-view">
      <section className="connect-card">
        <h1>Connect to Guuey</h1>
        <p>
          Enter the backend bearer token. It stays in memory for this tab and
          clears when you disconnect or reload.
        </p>
        <form className="connect-form" onSubmit={submit}>
          <label htmlFor="api-token">Backend token</label>
          <input
            id="api-token"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            disabled={connecting}
            required
          />
          {error && (
            <p className="error-message" role="alert">
              {error}
            </p>
          )}
          <button
            className="connect-button"
            type="submit"
            disabled={connecting}
          >
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
      </section>
    </main>
  );
}
