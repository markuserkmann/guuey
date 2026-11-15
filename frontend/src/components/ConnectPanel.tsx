import { useEffect, useState, type FormEvent } from "react";
import {
  DEV_SESSION,
  hasDevSession,
  signIn,
  signInWithMagicLink,
  tokenError,
  type AppSession,
} from "../auth";
import { verifySession } from "../chatApi";

export function ConnectPanel({
  onConnect,
  initialError = "",
}: {
  onConnect: (session: AppSession) => void;
  initialError?: string;
}) {
  const [method, setMethod] = useState<"password" | "magic" | "token">(
    "password",
  );
  const [username, setUsername] = useState("");
  const [value, setValue] = useState("");
  const [error, setError] = useState(initialError);
  const [connecting, setConnecting] = useState(false);
  const [development, setDevelopment] = useState(false);

  useEffect(() => {
    void hasDevSession().then(setDevelopment);
  }, []);

  function chooseMethod(next: typeof method) {
    setMethod(next);
    setValue("");
    setError("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (connecting) return;
    setConnecting(true);
    setError("");
    try {
      let session: AppSession;
      if (method === "password") session = await signIn(username.trim(), value);
      else if (method === "magic") session = await signInWithMagicLink(value);
      else {
        const token = value.trim();
        const validationError = tokenError(token);
        if (validationError) throw new Error(validationError);
        await verifySession(token);
        session = { token };
      }
      setValue("");
      onConnect(session);
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

  async function connectDevelopment() {
    setConnecting(true);
    setError("");
    try {
      await verifySession(DEV_SESSION);
      onConnect({ token: DEV_SESSION });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect.");
    } finally {
      setConnecting(false);
    }
  }

  return (
    <main className="connect-view">
      <section className="connect-card">
        <h1>Sign in to Guuey</h1>
        <p>
          Open your chats with your username and password or your personal magic
          link.
        </p>
        <div
          className="sign-in-methods"
          role="group"
          aria-label="Sign-in method"
        >
          <button
            type="button"
            aria-pressed={method === "password"}
            disabled={connecting}
            onClick={() => chooseMethod("password")}
          >
            Password
          </button>
          <button
            type="button"
            aria-pressed={method === "magic"}
            disabled={connecting}
            onClick={() => chooseMethod("magic")}
          >
            Magic link
          </button>
          <button
            type="button"
            aria-pressed={method === "token"}
            disabled={connecting}
            onClick={() => chooseMethod("token")}
          >
            API token
          </button>
        </div>
        <form className="connect-form" onSubmit={submit}>
          {method === "password" && (
            <>
              <label htmlFor="username">Username</label>
              <input
                id="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={64}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={connecting}
                required
              />
            </>
          )}
          <label htmlFor="sign-in-secret">
            {method === "password"
              ? "Password"
              : method === "magic"
                ? "Magic link"
                : "Backend token"}
          </label>
          <input
            key={method}
            id="sign-in-secret"
            type="password"
            autoComplete={method === "password" ? "current-password" : "off"}
            maxLength={method === "password" ? 1024 : 4096}
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
            {connecting ? "Signing in…" : "Sign in"}
          </button>
        </form>
        {method === "magic" && (
          <p className="sign-in-help">
            Use the link supplied with your account. It works again each time
            you sign in.
          </p>
        )}
        {method === "token" && (
          <p className="sign-in-help">
            API-token access opens the existing shared workspace for this tab.
          </p>
        )}
        {development && (
          <button
            className="quiet-button development-sign-in"
            type="button"
            disabled={connecting}
            onClick={() => void connectDevelopment()}
          >
            Use development connection
          </button>
        )}
      </section>
    </main>
  );
}
