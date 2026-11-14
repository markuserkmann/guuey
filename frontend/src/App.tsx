import { useEffect, useRef, useState, type FormEvent } from "react";
import { ConnectPanel } from "./components/ConnectPanel";
import { DebugPanel } from "./components/DebugPanel";
import { Message } from "./components/Message";
import { ChatSidebar } from "./components/ChatSidebar";
import { EmptyState } from "./components/EmptyState";
import { createChat, getHealth, listChats, loadChat, sendMessage, type ChatMessage, type ChatSummary, type Health, type ModelTrace } from "./chatApi";

export function App() {
  const [token, setToken] = useState("");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [searchEnabled, setSearchEnabled] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [chatReady, setChatReady] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [error, setError] = useState("");
  const [traces, setTraces] = useState<Record<string, ModelTrace[]>>({});
  const [debugOpen, setDebugOpen] = useState(false);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("guuey-sidebar-collapsed") === "true");
  const endRef = useRef<HTMLDivElement>(null);
  const loadSequence = useRef(0);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    if (!mobileHistoryOpen && !debugOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileHistoryOpen(false);
        setDebugOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileHistoryOpen, debugOpen]);

  useEffect(() => {
    if (!token) return;
    let current = true;
    const sequence = ++loadSequence.current;
    setChatReady(false);
    setLoadingChat(true);
    async function initialize() {
      try {
        const [items, status] = await Promise.all([listChats(token), getHealth()]);
        if (!current) return;
        setChats(items);
        setHealth(status);
        const selected = items[0] ?? await createChat(token);
        if (!current) return;
        if (items.length === 0) setChats([selected]);
        const chat = await loadChat(token, selected.id);
        if (!current || sequence !== loadSequence.current) return;
        setActiveId(selected.id);
        setMessages(chat.messages);
        setChatReady(true);
        setLoadingChat(false);
      } catch (cause) {
        if (current) {
          setError(cause instanceof Error ? cause.message : "Could not load chats.");
          setLoadingChat(false);
        }
      }
    }
    void initialize();
    return () => { current = false; };
  }, [token]);

  useEffect(() => {
    if (!token) return;
    let current = true;
    const timer = window.setTimeout(() => {
      listChats(token, query).then((items) => { if (current) setChats(items); })
        .catch(() => undefined);
    }, 180);
    return () => { current = false; window.clearTimeout(timer); };
  }, [token, query]);

  async function openChat(id: string) {
    if (!token || busy || loadingChat || id === activeId) return;
    const sequence = ++loadSequence.current;
    setChatReady(false);
    setLoadingChat(true);
    setError("");
    setActiveId(id);
    setMessages([]);
    setMobileHistoryOpen(false);
    try {
      const chat = await loadChat(token, id);
      if (sequence !== loadSequence.current) return;
      setMessages(chat.messages);
      setChatReady(true);
    } catch (cause) {
      if (sequence === loadSequence.current) setError(cause instanceof Error ? cause.message : "Could not load this chat.");
    } finally {
      if (sequence === loadSequence.current) setLoadingChat(false);
    }
  }

  async function newChat() {
    if (!token || busy || loadingChat) return;
    setError("");
    try {
      const chat = await createChat(token);
      setChats((current) => [chat, ...current.filter((item) => item.id !== chat.id)]);
      await openChat(chat.id);
      setDraft("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create a chat.");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!token || !activeId || !content || busy || !chatReady || loadingChat) return;
    setDraft("");
    setError("");
    setBusy(true);
    try {
      const result = await sendMessage(token, activeId, content, searchEnabled);
      setMessages((current) => [...current, result.user, result.assistant]);
      if (result.debug) setTraces((current) => ({ ...current, [activeId]: [...(current[activeId] ?? []), result.debug] }));
      const items = await listChats(token, query);
      setChats(items);
    } catch (cause) {
      setDraft(content);
      setError(cause instanceof Error ? cause.message : "The message could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  function disconnect() {
    ++loadSequence.current;
    setTraces({}); setToken(""); setMessages([]); setChats([]); setActiveId(""); setHealth(null); setChatReady(false);
  }

  if (!token) return <ConnectPanel onConnect={setToken} />;

  return (
    <div className={`workspace-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      {mobileHistoryOpen && <button className="mobile-sidebar-backdrop" aria-label="Close chat history" onClick={() => setMobileHistoryOpen(false)} />}
      <div className={`sidebar-wrap${mobileHistoryOpen ? " open" : ""}`}>
        <ChatSidebar onDisconnect={disconnect} onCollapse={() => { setSidebarCollapsed(true); localStorage.setItem("guuey-sidebar-collapsed", "true"); setMobileHistoryOpen(false); }} chats={chats} activeId={activeId} query={query} onQueryChange={setQuery} disabled={busy || loadingChat}
          onNewChat={() => void newChat()} onSelect={(id) => void openChat(id)} />
      </div>

      <main className="chat-main">
        <header className="chat-header">
          <div className="header-leading"><button className="quiet-button sidebar-expand" type="button" aria-label="Open sidebar" onClick={() => { setSidebarCollapsed(false); localStorage.setItem("guuey-sidebar-collapsed", "false"); }}>☰</button><a className="main-brand" href="/">Guuey</a><span className="chat-title">{chats.find((chat) => chat.id === activeId)?.title ?? "New chat"}</span></div>
          <div className="header-actions">
            <button className="quiet-button history-button" type="button" onClick={() => setMobileHistoryOpen(true)}>Chats</button>
            <button className="quiet-button" type="button" onClick={() => setDebugOpen(true)}>Debug</button>
          </div>
        </header>

        <section className="conversation" aria-label="Conversation" aria-live="polite">
          {!chatReady ? (
            <p className="waiting" role="status">{loadingChat ? "Opening chat…" : "Loading chats…"}</p>
          ) : messages.length === 0 ? (
            <EmptyState />
          ) : messages.map((message) => <Message key={message.id} message={message} />)}
          {busy && <p className="waiting" role="status">{searchEnabled ? "Searching with Exa, then preparing an answer…" : "Preparing a reply…"}</p>}
          {error && <p className="error-message" role="alert">{error}</p>}
          <div ref={endRef} />
        </section>

        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="message-input">Message</label>
          <textarea id="message-input" value={draft} onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault(); event.currentTarget.form?.requestSubmit();
              }
            }} placeholder={busy ? "Waiting for the current task…" : "Describe a new task…"} rows={1} disabled={busy || !chatReady || loadingChat} />
          <button className="send-button" type="submit" disabled={busy || !chatReady || loadingChat || !draft.trim()} aria-label="Send message">↑</button>
          <label className="research-toggle" title={health?.exa_configured && health.mode === "gemini" ? "Search the web with Exa before Gemini answers" : "Configure both EXA_API_KEY and GEMINI_API_KEY on the backend to enable web search"}>
            <input type="checkbox" checked={searchEnabled} disabled={!health?.exa_configured || health.mode !== "gemini" || busy}
              onChange={(event) => setSearchEnabled(event.target.checked)} />
            Search with Exa
          </label>
        </form>

        {debugOpen && <DebugPanel chatId={activeId} traces={traces[activeId] ?? []} onClose={() => setDebugOpen(false)} />}
      </main>
    </div>
  );
}
