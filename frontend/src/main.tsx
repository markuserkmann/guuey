import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

function App() {
  const [status, setStatus] = useState("Checking backend…");

  useEffect(() => {
    const controller = new AbortController();
    async function checkHealth() {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });
        if (!response.ok) throw new Error("Health check failed");
        const data: unknown = await response.json();
        if (!data || typeof data !== "object" || !("status" in data) || data.status !== "ok") {
          throw new Error("Unexpected health response");
        }
        setStatus("Backend connected.");
      } catch {
        if (!controller.signal.aborted) setStatus("Backend unavailable.");
      }
    }
    void checkHealth();
    return () => controller.abort();
  }, []);

  return (
    <main>
      <h1>Guuey</h1>
      <p role="status">{status}</p>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);
