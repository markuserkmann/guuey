import { useEffect, useRef, useState, type ReactNode } from "react";
import { restoreSession, signOut, type AppSession } from "../auth";
import { ConnectPanel } from "./ConnectPanel";

export function SessionGate({
  children,
}: {
  children: (session: AppSession, disconnect: () => Promise<void>) => ReactNode;
}) {
  const [session, setSession] = useState<AppSession | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    let active = true;
    function restore() {
      const attempt = ++sequence.current;
      setChecking(true);
      setError("");
      void restoreSession()
        .then((value) => {
          if (active && attempt === sequence.current) setSession(value);
        })
        .catch((cause) => {
          if (active && attempt === sequence.current) {
            setSession(null);
            setError(
              cause instanceof Error ? cause.message : "Could not sign in.",
            );
          }
        })
        .finally(() => {
          if (active && attempt === sequence.current) setChecking(false);
        });
    }
    restore();
    const hashChanged = () => {
      if (new URLSearchParams(window.location.hash.slice(1)).has("magic"))
        restore();
    };
    const expire = () => {
      ++sequence.current;
      setChecking(false);
      setSession(null);
      setError(
        "Your session ended or the account changed in another tab. Sign in again.",
      );
    };
    const changed = (event: StorageEvent) => {
      if (event.key === "guuey-account-change") expire();
    };
    window.addEventListener("guuey-session-expired", expire);
    window.addEventListener("storage", changed);
    window.addEventListener("hashchange", hashChanged);
    return () => {
      active = false;
      window.removeEventListener("guuey-session-expired", expire);
      window.removeEventListener("storage", changed);
      window.removeEventListener("hashchange", hashChanged);
    };
  }, []);

  async function disconnect() {
    await signOut();
    ++sequence.current;
    setSession(null);
    setError("");
    const url = new URL(window.location.href);
    url.searchParams.delete("chat");
    window.history.replaceState(null, "", url);
  }

  if (checking)
    return (
      <main className="connect-view">
        <p role="status">Signing in…</p>
      </main>
    );
  if (!session)
    return <ConnectPanel initialError={error} onConnect={setSession} />;
  return children(session, disconnect);
}
