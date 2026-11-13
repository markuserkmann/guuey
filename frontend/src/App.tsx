import { useEffect, useRef, useState, type FormEvent } from "react";
import { DebugPanel } from "./components/DebugPanel";
import { Message } from "./components/Message";
import { sendMessage, type ChatMessage } from "./chatApi";
import { ConnectPanel } from "./components/ConnectPanel";

export function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [token, setToken] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [debugOpen, setDebugOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [messages]);
  useEffect(() => {
    if (!debugOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDebugOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [debugOpen]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || busy) return;
    setDraft("");
    setError("");
    setBusy(true);
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content };
    setMessages((current) => [...current, userMessage]);
    try {
      const reply = await sendMessage(token, content, messages);
      setMessages((current) => [...current, reply]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The message could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  return (
    token ? (
    <main className="chat-shell">
      <header className="chat-header">
        <a className="wordmark" href="/" aria-label="Guuey home">Guuey</a>
        <div className="header-actions">
          <button className="quiet-button" type="button" onClick={() => setDebugOpen(true)}>Debug</button>
          <button className="quiet-button" type="button" onClick={() => { setToken(""); setMessages([]); }}>Disconnect</button>
        </div>
      </header>

      <section className="conversation" aria-label="Conversation" aria-live="polite">
        {messages.length === 0 ? (
          <div className="welcome">
            <h1>What can I help with?</h1>
            <p>Send a message to try the local chat flow.</p>
          </div>
        ) : (
          messages.map((message) => <Message key={message.id} message={message} />)
        )}
        {busy && <p className="waiting" role="status">Preparing a reply…</p>}
        {error && <p className="error-message" role="alert">{error}</p>}
        <div ref={endRef} />
      </section>

      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="message-input">Message</label>
        <textarea
          id="message-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Message Guuey"
          rows={1}
          disabled={busy}
        />
        <button className="send-button" type="submit" disabled={busy || !draft.trim()} aria-label="Send message">
          ↑
        </button>
      </form>
      <p className="composer-hint">Enter to send · Shift+Enter for a new line</p>

        {debugOpen && <DebugPanel onClose={() => setDebugOpen(false)} />}
    </main>
    ) : <ConnectPanel onConnect={setToken} />
  );
}
