import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import {
  confirmTelegram,
  disconnectTelegram,
  linkTelegram,
  telegramStatus,
  type TelegramStatus,
} from "../telegramApi";

export function TelegramSettings({
  token,
  hidden,
  onLinkedChange,
  onChanged,
  onChooseTasks,
}: {
  token: string;
  hidden: boolean;
  onLinkedChange: (linked: boolean) => void;
  onChanged: () => void;
  onChooseTasks: () => void;
}) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [link, setLink] = useState<{ id: string; url: string } | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const busy = Boolean(busyAction);
  const alive = useRef(false);
  const locked = useRef(false);
  const sequence = useRef(0);
  const openLink = useRef<HTMLAnchorElement>(null);
  const chooseTasks = useRef<HTMLButtonElement>(null);
  const focusAfterAction = useRef<"link" | "tasks" | null>(null);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const current = ++sequence.current;
      const result = await telegramStatus(token, signal);
      if (alive.current && !signal?.aborted && current === sequence.current) {
        setStatus(result);
        setLoadError("");
      }
    },
    [token],
  );

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    let timer: number;
    const update = async () => {
      try {
        if (!locked.current) await refresh(controller.signal);
      } catch {
        if (!controller.signal.aborted)
          setLoadError("Could not check your Telegram connection. Try again.");
      } finally {
        if (!controller.signal.aborted)
          timer = window.setTimeout(() => void update(), 3000);
      }
    };
    void update();
    return () => {
      alive.current = false;
      ++sequence.current;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [refresh]);

  useEffect(() => {
    onLinkedChange(Boolean(status?.linked));
  }, [status?.linked, onLinkedChange]);

  async function act(operation: () => Promise<void>, action = "refresh") {
    if (locked.current) return;
    locked.current = true;
    ++sequence.current;
    setBusyAction(action);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (cause) {
      if (alive.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not update Telegram. Try again.",
        );
    } finally {
      locked.current = false;
      if (alive.current) setBusyAction("");
    }
  }

  const pending = status?.pending;
  const candidate = pending?.candidate;
  const currentLink = pending?.id === link?.id ? link?.url : null;

  useEffect(() => {
    if (busy) return;
    if (focusAfterAction.current === "link" && currentLink) {
      openLink.current?.focus();
      focusAfterAction.current = null;
    } else if (focusAfterAction.current === "tasks" && status?.linked) {
      chooseTasks.current?.focus();
      focusAfterAction.current = null;
    }
  }, [busy, currentLink, status?.linked]);

  async function createLink() {
    const result = await linkTelegram(token);
    if (!alive.current) return;
    focusAfterAction.current = "link";
    setStatus(result);
    setLink(result.pending ? { id: result.pending.id, url: result.url } : null);
    onChanged();
  }

  async function disconnect() {
    await disconnectTelegram(token);
    if (!alive.current) return;
    const wasLinked = Boolean(status?.linked);
    setLink(null);
    onChanged();
    await refresh();
    if (alive.current)
      setNotice(
        wasLinked
          ? "Disconnected. Telegram is off for all your tasks."
          : "Link cancelled.",
      );
  }

  const identity = status?.linked ?? candidate;
  const connectionLabel = !status
    ? "Checking…"
    : !status.configured
      ? "Unavailable"
      : status.linked
        ? "Connected"
        : candidate
          ? "Confirm account"
          : pending
            ? "Waiting for you"
            : "Not connected";

  return (
    <section
      className="telegram-settings"
      hidden={hidden}
      aria-labelledby="telegram-title"
      aria-busy={busy}
    >
      <header className="telegram-heading">
        <h3 id="telegram-title">Telegram</h3>
        <span className="telegram-connection" role="status">
          {status?.linked && <Icon name="check" width="16" height="16" />}
          {connectionLabel}
        </span>
      </header>
      <p className="telegram-description">
        Receive task updates in a private chat.
      </p>
      {(error || loadError) && (
        <div className="task-error telegram-feedback" role="alert">
          <p>{error || loadError}</p>
          <button
            className="quiet-button"
            disabled={busy}
            onClick={() => void act(() => refresh())}
          >
            Try again
          </button>
        </div>
      )}
      {notice && (
        <p
          className={status?.linked ? "sr-only" : "telegram-feedback task-note"}
          role="status"
        >
          {notice}
        </p>
      )}
      {!status ? null : !status.configured ? (
        <p className="task-note">
          Ask your administrator to enable Telegram. Updates are still saved in
          Guuey.
        </p>
      ) : (
        <>
          {!status.worker_online && (
            <p className="task-warning telegram-feedback" role="status">
              Telegram is temporarily offline. Linking and delivery will resume
              automatically. Updates are saved in Guuey.
            </p>
          )}
          {identity && (
            <div className="telegram-account">
              <div className="telegram-identity">
                <p className="telegram-account-name">
                  <bdi>{identity.first_name}</bdi>
                </p>
                {identity.username && (
                  <p className="task-note">
                    @<bdi>{identity.username}</bdi>
                  </p>
                )}
                {!status.linked && (
                  <p className="task-note">
                    Telegram ID: {identity.telegram_id}
                  </p>
                )}
              </div>
              {status.linked && (
                <button
                  className="quiet-button"
                  disabled={busy}
                  aria-label="Disconnect Telegram"
                  onClick={() => void act(disconnect, "disconnect")}
                >
                  {busyAction === "disconnect"
                    ? "Disconnecting…"
                    : "Disconnect"}
                </button>
              )}
            </div>
          )}
          {status.linked ? (
            <>
              <div className="telegram-next">
                <p>
                  Enable “Send matches to Telegram” in the tasks you choose.
                </p>
                <button
                  ref={chooseTasks}
                  className="browser-primary"
                  disabled={busy}
                  onClick={onChooseTasks}
                >
                  Choose tasks
                </button>
              </div>
              {status.queued_count > 0 && (
                <p className="task-note" role="status">
                  {status.queued_count}{" "}
                  {status.queued_count === 1
                    ? "update waiting"
                    : "updates waiting"}{" "}
                  for delivery.
                </p>
              )}
              {status.delivery_issue && (
                <p className="task-warning telegram-feedback">
                  Last delivery issue — {status.delivery_issue.title}:{" "}
                  {status.delivery_issue.error}
                </p>
              )}
              <details className="telegram-details">
                <summary>Connection details</summary>
                <p>Telegram ID: {status.linked.telegram_id}</p>
                <p>
                  Disconnect here or send /stop to the bot to turn off Telegram
                  for all your tasks. A message already being sent may still
                  arrive.
                </p>
              </details>
            </>
          ) : candidate && pending ? (
            <>
              <p className="telegram-instruction">
                Is this your account? Results from the tasks you enable will be
                shared here.
              </p>
              <div className="telegram-actions">
                <button
                  className="browser-primary"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = await confirmTelegram(token, pending.id);
                      if (!alive.current) return;
                      focusAfterAction.current = "tasks";
                      setStatus(result);
                      setLink(null);
                      setNotice(
                        "Telegram connected. Choose which tasks can send updates.",
                      );
                    }, "confirm")
                  }
                >
                  {busyAction === "confirm"
                    ? "Linking…"
                    : "Confirm this account"}
                </button>
                <button
                  className="quiet-button"
                  disabled={busy}
                  onClick={() => void act(disconnect, "disconnect")}
                >
                  Cancel link
                </button>
              </div>
            </>
          ) : (
            <>
              {pending && currentLink ? (
                <>
                  <p className="telegram-instruction">
                    Press Start in @{status.bot_username}, then return here to
                    confirm your account.
                  </p>
                  <div className="telegram-actions">
                    <a
                      ref={openLink}
                      className="browser-primary telegram-open"
                      href={currentLink}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Telegram
                    </a>
                    <button
                      className="quiet-button"
                      disabled={busy}
                      onClick={() => void act(disconnect, "disconnect")}
                    >
                      Cancel link
                    </button>
                  </div>
                  <div className="telegram-link-footer">
                    <p className="task-note">
                      Private link · Expires at{" "}
                      {new Date(pending.expires_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </p>
                    <button
                      className="quiet-button"
                      disabled={busy}
                      onClick={() => void act(createLink, "link")}
                    >
                      {busyAction === "link"
                        ? "Creating link…"
                        : "Create new link"}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="telegram-instruction">
                    {pending
                      ? "Press Start in the bot chat you opened, or create a new link."
                      : "Link your account, then choose which tasks can send updates."}
                  </p>
                  {link && !pending && (
                    <p className="task-warning" role="status">
                      Your link expired or was cancelled. Create a new one to
                      continue.
                    </p>
                  )}
                  <div className="telegram-actions">
                    <button
                      className="browser-primary"
                      disabled={busy}
                      onClick={() => void act(createLink, "link")}
                    >
                      {busyAction === "link"
                        ? "Creating link…"
                        : pending || link
                          ? "Create new link"
                          : "Link Telegram"}
                    </button>
                    {pending && (
                      <button
                        className="quiet-button"
                        disabled={busy}
                        onClick={() => void act(disconnect, "disconnect")}
                      >
                        Cancel link
                      </button>
                    )}
                  </div>
                  {!pending && (
                    <p className="task-note telegram-consent">
                      Nothing is sent until you enable a task.
                    </p>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
