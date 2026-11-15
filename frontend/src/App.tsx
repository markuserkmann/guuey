import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import Markdown from "react-markdown";
import { SessionGate } from "./components/SessionGate";
import { DebugPanel } from "./components/DebugPanel";
import type { AppSession } from "./auth";
import { ChatSidebar } from "./components/ChatSidebar";
import { EmptyState } from "./components/EmptyState";
import { Composer } from "./components/Composer";
import { Icon } from "./components/Icon";
import { BrowserActivity } from "./components/BrowserActivity";
import { BrowserRequestCard } from "./components/BrowserRequestCard";
import { RunActivity } from "./components/RunActivity";
import { SavedLogins } from "./components/SavedLogins";
import { RecurringTasks } from "./components/RecurringTasks";
import {
  latestTaskRun,
  listTasks,
  taskInbox,
  type RecurringTask,
} from "./taskApi";
import { isBrowserRun } from "./browserApi";
import { streamRun } from "./runStream";
import { isRunActive, runMessages } from "./runPresentation";
import type { TextAttachment } from "./attachments";
import {
  createChat,
  pinChat,
  runError,
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
  forceBrowser: boolean;
  failed: boolean;
};

export function App() {
  return (
    <SessionGate>
      {(session, disconnect) => (
        <Workspace
          key={session.token}
          session={session}
          disconnect={disconnect}
        />
      )}
    </SessionGate>
  );
}

function Workspace({
  session,
  disconnect,
}: {
  session: AppSession;
  disconnect: () => Promise<void>;
}) {
  const { token, user } = session;
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const submitLock = useRef(false);
  const runGeneration = useRef(0);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<TextAttachment[]>([]);
  const [mode, setMode] = useState<ResearchRun["mode"]>("auto");
  const [forceBrowser, setForceBrowser] = useState(false);
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [chatReady, setChatReady] = useState(false);
  const [loadingChat, setLoadingChat] = useState(false);
  const [error, setError] = useState("");
  const [traces, setTraces] = useState<Record<string, ModelTrace[]>>({});
  const [debugOpen, setDebugOpen] = useState(false);
  const [loginsOpen, setLoginsOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [taskRevision, setTaskRevision] = useState(0);
  const [unreadUpdates, setUnreadUpdates] = useState(0);
  const [tasks, setTasks] = useState<RecurringTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState("");
  const activeTask = tasks.find((task) => task.chat_id === activeId);
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
    if (loginsOpen || tasksOpen) return;
    if (!mobileHistoryOpen && !debugOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileHistoryOpen(false);
        if (debugOpen) closeDebug();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileHistoryOpen, debugOpen, loginsOpen, tasksOpen]);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    let timer: number;
    async function refresh() {
      if (pending) return;
      pending = true;
      window.clearTimeout(timer);
      const [saved, inbox] = await Promise.allSettled([
        listTasks(token, controller.signal),
        taskInbox(token, controller.signal),
      ]);
      pending = false;
      if (controller.signal.aborted) return;
      setTasksLoading(false);
      if (saved.status === "fulfilled") {
        setTasks(saved.value);
        setTasksError("");
      } else {
        setTasksError("Could not refresh tasks.");
      }
      if (inbox.status === "fulfilled")
        setUnreadUpdates(inbox.value.unread_count);
      const checking =
        saved.status === "fulfilled" &&
        saved.value.some((task) =>
          ["queued", "running"].includes(task.last_status ?? ""),
        );
      timer = window.setTimeout(refresh, checking ? 3000 : 10000);
    }
    void refresh();
    window.addEventListener("focus", refresh);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [token, taskRevision]);

  async function openTaskChat(id: string) {
    if (submission || loadingChat)
      throw new Error(
        "Wait for the current message to be sent or the chat to load, then try again.",
      );
    const [chat, items] = await Promise.all([
      loadChat(token, id),
      listChats(token, query),
    ]);
    if (id === activeId) {
      setChats(items);
      setMessages(chat.messages);
      return;
    }
    ++loadSequence.current;
    ++runGeneration.current;
    setActiveId(id);
    setChats(items);
    setMessages(chat.messages);
    setRun(null);
    setReconnecting(false);
    setBusy(false);
    setError("");
    setDraft("");
    setAttachments([]);
    setChatReady(true);
    setMobileHistoryOpen(false);
  }

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
        const requestedId = new URL(window.location.href).searchParams.get(
          "chat",
        );
        const selected =
          items.find((item) => item.id === requestedId) ??
          items[0] ??
          (await createChat(token));
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
    setReconnecting(false);
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
    const restore = activeTask
      ? latestTaskRun(token, activeId, activeTask.id)
      : latestRun(token, activeId);
    restore
      .then((value) => {
        if (current && generation === runGeneration.current) {
          setRun((existing) => (existing?.id === value?.id ? existing : value));
        }
      })
      .catch(() => {
        if (current) setError("Could not restore run status.");
      });
    return () => {
      current = false;
    };
  }, [token, activeId, activeTask?.id, activeTask?.last_run_at]);

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
        setReconnecting(false);
        const pending = value.status === "queued" || value.status === "running";
        if (pending) {
          setBusy(true);
          setError("");
          await streamRun(token, value.id, controller.signal, (snapshot) => {
            if (!current) return;
            setReconnecting(false);
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
          setReconnecting(false);
          setBusy(false);
          setError(runError(value));
          setChats(items);
        }
      } catch {
        if (current) {
          setReconnecting(true);
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
    setReconnecting(false);
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
        next.forceBrowser,
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
      forceBrowser: forceBrowser && !!health?.browser_enabled,
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
    else if (run?.chat_id === activeId) return runMessages(items, run);
    return items;
  }, [messages, submission, activeId, run]);

  async function runAction(action: "continue" | "cancel") {
    if (!run) return;
    const generation = runGeneration.current;
    setError("");
    try {
      const value = await (action === "continue"
        ? continueRun(token, run.id)
        : cancelRun(token, run.id));
      if (generation !== runGeneration.current) return;
      setRun(value);
      setBusy(value.status === "queued" || value.status === "running");
    } catch (cause) {
      if (generation !== runGeneration.current) return;
      setError(cause instanceof Error ? cause.message : "Run action failed.");
    }
  }

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
          username={user?.username}
          onDisconnect={() =>
            void disconnect().catch(() =>
              setError(
                "Could not sign out. Check your connection and try again.",
              ),
            )
          }
          onPin={(id, pinned) => void togglePin(id, pinned)}
          runningChatId={busy ? activeId : undefined}
          tasks={tasks}
          tasksLoading={tasksLoading}
          tasksError={tasksError}
          unreadUpdates={unreadUpdates}
          onOpenTasks={() => setTasksOpen(true)}
          onRetryTasks={() => setTaskRevision((value) => value + 1)}
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
              {activeTask?.title ??
                chats.find((chat) => chat.id === activeId)?.title ??
                "New chat"}
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
              className="quiet-button"
              type="button"
              aria-haspopup="dialog"
              onClick={() => setLoginsOpen(true)}
            >
              Saved logins
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
            activeMessageId={
              run?.chat_id === activeId ? `assistant:${run.id}` : undefined
            }
            activity={
              run?.chat_id === activeId && (
                <RunActivity
                  key={run.id}
                  run={run}
                  reconnecting={reconnecting}
                  onStop={() => runAction("cancel")}
                >
                  {isRunActive(run) && run.live_output?.kind === "progress" && (
                    <div className="run-progress-note">
                      <Markdown
                        disallowedElements={["img", "a"]}
                        unwrapDisallowed
                      >
                        {run.live_output.text}
                      </Markdown>
                    </div>
                  )}
                  {run.browser && (
                    <details
                      className="run-details"
                      open={
                        run.browser.actions.some(
                          (action) => action.status === "unverified",
                        ) || undefined
                      }
                    >
                      <summary>
                        Browser activity
                        {run.browser.steps.length
                          ? ` · ${run.browser.steps.length} ${run.browser.steps.length === 1 ? "step" : "steps"}`
                          : ""}
                      </summary>
                      <BrowserActivity
                        state={run.browser}
                        onReview={() => setLoginsOpen(true)}
                      />
                    </details>
                  )}
                  {run.status === "running" && run.browser?.request && (
                    <BrowserRequestCard
                      key={`${run.id}:${run.browser.request.id}`}
                      token={token}
                      request={run.browser.request}
                      liveView={run.browser.live_view_url}
                      onRun={(value) =>
                        setRun((current) =>
                          current?.id === value.id ? value : current,
                        )
                      }
                      onCancel={() => runAction("cancel")}
                    />
                  )}
                </RunActivity>
              )
            }
          >
            {!chatReady ? (
              <p className="waiting" role="status">
                {loadingChat ? "Opening chat…" : "Loading chats…"}
              </p>
            ) : visibleMessages.length === 0 ? (
              <EmptyState />
            ) : null}
            {busy && !run && (
              <div className="waiting">
                <p role="status">
                  <span className="activity-spinner" aria-hidden="true" />
                  Sending message…
                </p>
              </div>
            )}
            {!busy &&
              run?.chat_id === activeId &&
              run.status === "partial" &&
              run.mode !== "deep" &&
              !isBrowserRun(run) && (
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
            {!busy &&
              run?.chat_id === activeId &&
              run.status === "failed" &&
              !isBrowserRun(run) && (
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
                      forceBrowser: run.force_browser ?? false,
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
          token={token}
          draft={draft}
          onDraftChange={setDraft}
          attachments={attachments}
          onAttachmentsChange={setAttachments}
          mode={mode}
          onModeChange={setMode}
          forceBrowser={forceBrowser && !!health?.browser_enabled}
          onForceBrowserChange={setForceBrowser}
          browserAvailable={!!health?.browser_enabled}
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
      {loginsOpen && (
        <SavedLogins
          token={token}
          enabled={!!health?.saved_logins_enabled}
          onClose={() => setLoginsOpen(false)}
          onChanged={() => {
            if (run)
              void getRun(token, run.id)
                .then((value) =>
                  setRun((current) =>
                    current?.id === value.id ? value : current,
                  ),
                )
                .catch(() => undefined);
          }}
        />
      )}
      {tasksOpen && (
        <RecurringTasks
          token={token}
          researchReady={health?.mode === "gemini" && !!health.exa_configured}
          onClose={() => setTasksOpen(false)}
          onChanged={() => {
            setTaskRevision((value) => value + 1);
            void listChats(token, query)
              .then(setChats)
              .catch(() => undefined);
          }}
          onOpenChat={openTaskChat}
        />
      )}
    </div>
  );
}
