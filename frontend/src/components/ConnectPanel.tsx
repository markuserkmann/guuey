import { useState, type FormEvent } from "react";

export function ConnectPanel({ onConnect }: { onConnect: (token: string) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = value.trim();
    if (token.length < 32) {
      setError("Enter the backend token. It must be at least 32 characters.");
      return;
    }
    onConnect(token);
    setValue("");
  }

  return (
    <main className="connect-view">
      <section className="connect-card">
        <h1>Connect to Guuey</h1>
        <p>Enter the backend bearer token. It stays in memory for this tab and clears when you disconnect or reload.</p>
        <form className="connect-form" onSubmit={submit}>
          <label htmlFor="api-token">Backend token</label>
          <input id="api-token" type="password" autoComplete="off" value={value} onChange={(event) => setValue(event.target.value)} required />
          {error && <p className="error-message" role="alert">{error}</p>}
          <button className="connect-button" type="submit">Connect</button>
        </form>
      </section>
    </main>
  );
}
