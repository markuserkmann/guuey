import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ConnectPanel } from "./components/ConnectPanel";
import { DebugPanel } from "./components/DebugPanel";
import { DEV_SESSION, hasDevSession } from "./auth";
import { ChatSidebar } from "./components/ChatSidebar";
import { EmptyState } from "./components/EmptyState";
import { Composer } from "./components/Composer";
import { Icon } from "./components/Icon";
import { streamRun } from "./runStream";
import type { TextAttachment } from "./attachments";
import {
  createChat,
  pinChat,
  runError,
  runProgress,
  getHealth,
  listChats,
  loadChat,
  startRun,
  getRun,
  latestRun,
  continueRun,
  cancelRun,
  type ResearchRun,
  type ChatMessage,
  type ChatSummary,
  type Health,
  type ModelTrace,
} from "./chatApi";

const Conversation = lazy(() =>
  import("./components/Conversation").then((module) => ({
    default: module.Conversation,
  })),
);

type Submission = {
  requestId: string;
  chatId: string;
  content: string;
  attachments: TextAttachment[];
  mode: ResearchRun["mode"];
  failed: boolean;
};

export function App() {
  const [token, setToken] = useState("");
  const [checkingSession, setCheckingSession] = useState(true);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [syncedRunId, setSyncedRunId] = useState("");
  const submitLock = useRef(false);
  const runGeneration = useRef(0);
  useEffect(() => {
    let active = true;
    void hasDevSession().then((enabled) => {
      if (active) {
        if (enabled) setToken(DEV_SESSION);
        setCheckingSession(false);
      }
    });
    return () => {
      active = false;
    };
  }, []);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<TextAttachment[]>([]);
  const [mode, setMode] = useState<ResearchRun["mode"]>("auto");
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [chatReady, setChatReady] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [error, setError] = useState("");
  const [traces, setTraces] = useState<Record<string, ModelTrace[]>>({});
  const [debugOpen, setDebugOpen] = useState(false);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem("guuey-sidebar-collapsed") === "true",
  );
  const loadSequence = useRef(0);
  const debugTrigger = useRef<HTMLButtonElement>(null);

  function closeDebug() {
    setDebugOpen(false);
    debugTrigger.current?.focus();
  }

  useEffect(() => {
    if (!mobileHistoryOpen && !debugOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileHistoryOpen(false);
        if (debugOpen) closeDebug();
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
        const [items, status] = await Promise.all([
          listChats(token),
          getHealth(),
        ]);
        if (!current) return;
        setChats(items);
        setHealth(status);
        const requestedId = new URL(window.location.href).searchParams.get("chat");
        const selected = items.find((item) => item.id === requestedId) ?? items[0] ?? (await createChat(token));
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
          setError(
            cause instanceof Error ? cause.message : "Could not load chats.",
          );
          setLoadingChat(false);
        }
      }
    }
    void initialize();
    return () => {
      current = false;
    };
  }, [token]);

  useEffect(() => {
    if (!activeId) return;
    const url = new URL(window.location.href);
    url.searchParams.set("chat", activeId);
    window.history.replaceState(null, "", url);
  }, [activeId]);

  useEffect(() => {
    if (!token) return;
    let current = true;
    const timer = window.setTimeout(() => {
      listChats(token, query)
        .then((items) => {
          if (current) setChats(items);
        })
        .catch(() => undefined);
    }, 180);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [token, query]);

  async function openChat(id: string) {
    if (!token || submission || loadingChat || id === activeId) return;
    const sequence = ++loadSequence.current;
    setChatReady(false);
    setLoadingChat(true);
    setError("");
    setActiveId(id);
    runGeneration.current += 1;
    setSubmission(null);
    setSyncedRunId("");
    setRun(null);
    setBusy(false);
    setDraft("");
    setAttachments([]);
    setMessages([]);
    setMobileHistoryOpen(false);
    try {
      const chat = await loadChat(token, id);
      if (sequence !== loadSequence.current) return;
      setMessages(chat.messages);
      setChatReady(true);
    } catch (cause) {
      if (sequence === loadSequence.current)
        setError(
          cause instanceof Error ? cause.message : "Could not load this chat.",
        );
    } finally {
      if (sequence === loadSequence.current) setLoadingChat(false);
    }
  }

  async function newChat() {
    if (!token || submission || loadingChat) return;
    setError("");
    try {
      const chat = await createChat(token);
      setChats((current) => [
        chat,
        ...current.filter((item) => item.id !== chat.id),
      ]);
      await openChat(chat.id);
      setDraft("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not create a chat.",
      );
    }
  }

  useEffect(() => {
    if (!token || !activeId) return;
    let current = true;
    const generation = runGeneration.current;
    latestRun(token, activeId)
      .then((value) => {
        if (current && generation === runGeneration.current) {
          setRun((existing) =>
            existing?.chat_id === activeId ? existing : value,
          );
          if (value && value.status !== "queued" && value.status !== "running")
            setSyncedRunId(value.id);
        }
      })
      .catch(() => {
        if (current) setError("Could not restore run status.");
      });
    return () => {
      current = false;
    };
  }, [token, activeId]);

  useEffect(() => {
    if (!run || !token || run.chat_id !== activeId) return;
    let current = true;
    let timer: number;
    const controller = new AbortController();
    async function refresh() {
      try {
        let value = await getRun(token, run!.id);
        if (!current) return;
        setRun(value);
        const pending = value.status === "queued" || value.status === "running";
        if (pending) {
          setBusy(true);
          setError("");
          await streamRun(token, value.id, controller.signal, (snapshot) => {
            if (!current) return;
            setRun((existing) =>
              existing?.id === snapshot.id
                ? { ...existing, ...snapshot }
                : existing,
            );
          });
          if (!current) return;
          value = await getRun(token, value.id);
          if (!current) return;
          if (value.status === "queued" || value.status === "running")
            throw new Error("Live connection interrupted");
          setRun(value);
        }
        setTraces((existing) => ({
          ...existing,
          [activeId]: value.debug.stages ?? [],
        }));
        const [chat, items] = await Promise.all([
          loadChat(token, activeId),
          listChats(token, query),
        ]);
        if (current) {
          setMessages(chat.messages);
          setSyncedRunId(value.id);
          setBusy(false);
          setError(runError(value));
          setChats(items);
        }
      } catch {
        if (current) {
          setError("Connection interrupted. Reconnecting to the saved run…");
          timer = window.setTimeout(refresh, 3000);
        }
      }
    }
    setBusy(run.status === "queued" || run.status === "running");
    void refresh();
    return () => {
      current = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [run?.id, token, activeId]);

  useEffect(() => {
    if (!debugOpen || !busy || !run || !token) return;
    let current = true;
    const timer = window.setInterval(() => {
      getRun(token, run.id)
        .then((value) => {
          if (!current) return;
          setRun((existing) =>
            existing?.id === value.id
              ? { ...existing, debug: value.debug }
              : existing,
          );
          setTraces((existing) => ({
            ...existing,
            [value.chat_id]: value.debug.stages ?? [],
          }));
        })
        .catch(() => undefined);
    }, 3000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [debugOpen, busy, run?.id, token]);

  async function sendSubmission(next: Submission) {
    if (submitLock.current) return;
    submitLock.current = true;
    runGeneration.current += 1;
    setError("");
    setBusy(true);
    setSubmission({ ...next, failed: false });
    setRun(null);
    setSyncedRunId("");
    setDraft("");
    setAttachments([]);
    try {
      const value = await startRun(
        token,
        next.chatId,
        next.content,
        next.mode,
        next.requestId,
        next.attachments,
      );
      setRun(value);
      setSubmission(null);
    } catch (cause) {
      setSubmission({ ...next, failed: true });
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not send. Retry to send this message.",
      );
      setBusy(false);
    } finally {
      submitLock.current = false;
    }
  }

  async function sendContent(content: string) {
    if (
      !token ||
      !activeId ||
      !content.trim() ||
      busy ||
      !chatReady ||
      loadingChat ||
      submission
    )
      return;
    await sendSubmission({
      requestId: crypto.randomUUID(),
      chatId: activeId,
      content: content.trim(),
      mode,
      attachments,
      failed: false,
    });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendContent(draft);
  }

  async function togglePin(id: string, pinned: boolean) {
    try {
      const updated = await pinChat(token, id, pinned);
      setChats((current) =>
        current.map((chat) => (chat.id === id ? updated : chat)),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not pin chat.");
    }
  }

  const visibleMessages = useMemo(() => {
    const items = [...messages];
    if (submission?.chatId === activeId)
      items.push({
        id: `user:${submission.requestId}`,
        role: "user",
        content: submission.content,
        attachments: submission.attachments,
        delivery: submission.failed ? "failed" : "sending",
      });
    else if (
      run?.chat_id === activeId &&
      (syncedRunId !== run.id || !run.result)
    ) {
      const userId = `user:${run.request_id}`;
      if (!run.parent_id && !items.some((item) => item.id === userId))
        items.push({
          id: userId,
          role: "user",
          content: run.message,
          attachments: run.attachments,
          delivery: "sent",
        });
      const liveAnswer =
        run.live_output?.kind !== "progress" ? run.live_output : null;
      if (
        (run.result || liveAnswer?.text) &&
        !items.some((item) => item.id === `assistant:${run.id}`)
      )
        items.push({
          id: `assistant:${run.id}`,
          role: "assistant",
          content: liveAnswer?.text || run.result?.reply || "",
          sources: liveAnswer?.text ? [] : run.result?.sources,
          delivery: busy ? "updating" : undefined,
          draft: liveAnswer?.kind === "draft",
        });
    }
    return items;
  }, [messages, submission, activeId, run, syncedRunId, busy]);

  async function runAction(action: "continue" | "cancel") {
    if (!run) return;
    setError("");
    try {
      const value = await (action === "continue"
        ? continueRun(token, run.id)
        : cancelRun(token, run.id));
      setRun(value);
      setBusy(value.status === "queued" || value.status === "running");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Run action failed.");
    }
  }

  function disconnect() {
    ++loadSequence.current;
    setAttachments([]);
    setDraft("");
    setDebugOpen(false);
    setError("");
    setRun(null);
    setTraces({});
    setToken("");
    setSubmission(null);
    setMessages([]);
    setChats([]);
    setActiveId("");
    setHealth(null);
    setChatReady(false);
  }

  if (checkingSession)
    return (
      <main className="connect-view">
        <p role="status">Connecting…</p>
      </main>
    );
  if (!token) return <ConnectPanel onConnect={setToken} />;

  return (
    <div
      className={`workspace-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}${debugOpen ? " debug-open" : ""}`}
    >
      {mobileHistoryOpen && (
        <button
          className="mobile-sidebar-backdrop"
          aria-label="Close chat history"
          onClick={() => setMobileHistoryOpen(false)}
        />
      )}
      <div className={`sidebar-wrap${mobileHistoryOpen ? " open" : ""}`}>
        <ChatSidebar
          onDisconnect={token === DEV_SESSION ? undefined : disconnect}
          onPin={(id, pinned) => void togglePin(id, pinned)}
          runningChatId={busy ? activeId : undefined}
          onCollapse={() => {
            setSidebarCollapsed(true);
            localStorage.setItem("guuey-sidebar-collapsed", "true");
            setMobileHistoryOpen(false);
          }}
          chats={chats}
          activeId={activeId}
          query={query}
          onQueryChange={setQuery}
          disabled={!!submission || loadingChat}
          onNewChat={() => void newChat()}
          onSelect={(id) => void openChat(id)}
        />
      </div>

      <main className="chat-main">
        <header className="chat-header">
          <div className="header-leading">
            <button
              className="icon-button sidebar-expand"
              type="button"
              aria-label="Open sidebar"
              onClick={() => {
                setSidebarCollapsed(false);
                localStorage.setItem("guuey-sidebar-collapsed", "false");
              }}
            >
              <Icon name="sidebar" />
            </button>
            <a className="main-brand" href="/">
              Guuey
            </a>
            <span className="chat-title">
              {chats.find((chat) => chat.id === activeId)?.title ?? "New chat"}
            </span>
          </div>
          <div className="header-actions">
            <button
              className="quiet-button history-button"
              type="button"
              onClick={() => setMobileHistoryOpen(true)}
            >
              Chats
            </button>
            <button
              ref={debugTrigger}
              className="quiet-button debug-toggle"
              type="button"
              aria-expanded={debugOpen}
              aria-controls="debug-panel"
              onClick={() => setDebugOpen((open) => !open)}
            >
              <Icon name="debug" />
              Debug
            </button>
          </div>
        </header>

        <Suspense
          fallback={
            <div className="conversation-root">
              <p className="waiting" role="status">
                Opening conversation…
              </p>
            </div>
          }
        >
          <Conversation
            key={activeId}
            messages={visibleMessages}
            busy={busy}
            onSend={sendContent}
            onRetry={() => {
              if (submission) void sendSubmission(submission);
            }}
            onCancel={() => runAction("cancel")}
          >
            {!chatReady ? (
              <p className="waiting" role="status">
                {loadingChat ? "Opening chat…" : "Loading chats…"}
              </p>
            ) : visibleMessages.length === 0 ? (
              <EmptyState />
            ) : null}
            {busy && (
              <div className="waiting">
                <p role="status">
                  <span className="activity-spinner" aria-hidden="true" />
                  {submission ? "Sending message…" : runProgress(run)}
                </p>
                {run?.live_output?.kind === "progress" && (
                  <p className="research-live-progress">
                    {run.live_output.text}
                  </p>
                )}
                <button
                  className="quiet-button"
                  type="button"
                  disabled={!run}
                  onClick={() => void runAction("cancel")}
                >
                  Stop
                </button>
              </div>
            )}
            {!busy &&
              run?.chat_id === activeId &&
              run.status === "partial" &&
              run.mode !== "deep" &&
              run.phase !== "browser_unavailable" && (
                <div className="waiting">
                  <p>Some requirements may need more research.</p>
                  <button
                    className="quiet-button"
                    type="button"
                    onClick={() => void runAction("continue")}
                  >
                    Continue with Deep research · up to 3 minutes
                  </button>
                </div>
              )}
            {!busy && run?.chat_id === activeId && run.status === "failed" && (
              <button
                className="quiet-button"
                type="button"
                onClick={() =>
                  void sendSubmission({
                    requestId: crypto.randomUUID(),
                    chatId: activeId,
                    content: run.message,
                    attachments: run.attachments ?? [],
                    mode: run.mode,
                    failed: false,
                  })
                }
              >
                Try again
              </button>
            )}
            {error && (
              <p className="error-message" role="alert">
                {error}
                {(run?.debug.error?.code === "billing_required" ||
                  run?.debug.error?.status_code === 402) && (
                  <>
                    {" "}
                    <a
                      href="https://ai.studio/projects"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Google AI Studio
                    </a>
                  </>
                )}
              </p>
            )}
          </Conversation>
        </Suspense>

        <Composer
          key={activeId}
          draft={draft}
          onDraftChange={setDraft}
          attachments={attachments}
          onAttachmentsChange={setAttachments}
          mode={mode}
          onModeChange={setMode}
          onSubmit={submit}
          disabled={busy || !chatReady || loadingChat || !!submission}
          busy={busy}
          deepAvailable={health?.mode === "gemini"}
        />
      </main>
      {debugOpen && (
        <DebugPanel
          run={run?.chat_id === activeId ? run : null}
          chatId={activeId}
          traces={traces[activeId] ?? []}
          onClose={closeDebug}
        />
      )}
    </div>
  );
}
