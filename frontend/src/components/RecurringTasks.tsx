import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Icon } from "./Icon";
import { TaskActivity } from "./TaskActivity";
import { TelegramSettings } from "./TelegramSettings";
import {
  checkTask,
  deleteTask,
  enableTask,
  listTasks,
  readAllTaskUpdates,
  readTaskUpdate,
  saveTask,
  taskFrequency,
  taskHistory,
  taskInbox,
  taskOutcome,
  taskTemplates,
  type RecurringTask,
  type TaskCheck,
  type TaskInbox,
  type TaskInput,
  type TaskTemplate,
} from "../taskApi";
import "./recurringTasks.css";

function dateLabel(value: string) {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function blankTask(): TaskInput {
  return {
    title: "",
    prompt: "",
    notification_condition: "",
    interval_minutes: 60,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    enabled: true,
    telegram_enabled: false,
  };
}

function TaskForm({
  initial,
  editing,
  working,
  telegramLinked,
  onSave,
  onCancel,
}: {
  initial: TaskInput;
  editing: boolean;
  working: boolean;
  telegramLinked: boolean;
  onSave: (input: TaskInput) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [custom, setCustom] = useState(
    ![1, 60, 360, 1440, 10080].includes(initial.interval_minutes),
  );
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSave({
      ...value,
      title: value.title.trim(),
      prompt: value.prompt.trim(),
      notification_condition: value.notification_condition.trim(),
      timezone: value.timezone.trim(),
    });
  }
  return (
    <form onSubmit={submit} className="task-form">
      <h3>{editing ? "Edit task" : "New recurring task"}</h3>
      <fieldset className="browser-fields" disabled={working}>
        <legend className="sr-only">Task instructions and schedule</legend>
        <label>
          Task name
          <input
            autoFocus
            required
            maxLength={120}
            value={value.title}
            onChange={(event) =>
              setValue({ ...value, title: event.target.value })
            }
          />
        </label>
        <label>
          What should Guuey check?
          <textarea
            required
            rows={4}
            maxLength={8000}
            value={value.prompt}
            onChange={(event) =>
              setValue({ ...value, prompt: event.target.value })
            }
          />
        </label>
        <label>
          Notify me when
          <textarea
            required
            rows={3}
            maxLength={2000}
            aria-describedby="task-condition-help"
            value={value.notification_condition}
            onChange={(event) =>
              setValue({ ...value, notification_condition: event.target.value })
            }
          />
        </label>
        <p id="task-condition-help" className="task-note">
          Only new matching results appear in Updates and this task’s chat.
          Checks with no matches stay in its history.
        </p>
        <div className="task-form-grid">
          <label>
            Repeat
            <select
              value={custom ? "custom" : value.interval_minutes}
              onChange={(event) => {
                setCustom(event.target.value === "custom");
                if (event.target.value !== "custom")
                  setValue({
                    ...value,
                    interval_minutes: Number(event.target.value),
                  });
              }}
            >
              <option value={1}>Every minute</option>
              <option value={60}>Every hour</option>
              <option value={360}>Every 6 hours</option>
              <option value={1440}>Every day</option>
              <option value={10080}>Every week</option>
              <option value="custom">Custom interval</option>
            </select>
          </label>
          {custom && (
            <label>
              Interval in minutes
              <input
                type="number"
                min={1}
                max={10080}
                step={1}
                required
                value={value.interval_minutes}
                onChange={(event) =>
                  setValue({
                    ...value,
                    interval_minutes: Number(event.target.value),
                  })
                }
              />
            </label>
          )}
          <label>
            Time zone
            <input
              required
              maxLength={100}
              value={value.timezone}
              onChange={(event) =>
                setValue({ ...value, timezone: event.target.value })
              }
              aria-describedby="task-timing-help"
            />
          </label>
        </div>
        <label className="task-checkbox">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(event) =>
              setValue({ ...value, enabled: event.target.checked })
            }
          />
          Run automatically
        </label>
        <label className="task-checkbox">
          <input
            type="checkbox"
            checked={value.telegram_enabled}
            disabled={!telegramLinked && !value.telegram_enabled}
            aria-describedby="task-telegram-help"
            onChange={(event) =>
              setValue({ ...value, telegram_enabled: event.target.checked })
            }
          />
          Send matches to Telegram
        </label>
        <p className="task-note" id="task-telegram-help">
          {telegramLinked
            ? "Only new matches are sent to your private chat."
            : "Save this task, then link your account in the Telegram tab."}
        </p>
      </fieldset>
      <p className="task-note" id="task-timing-help">
        Intervals use elapsed time. The first check runs after one interval;
        choose Check now after saving to run it sooner. Checks continue while
        the app is closed, as long as Guuey’s worker is running.
      </p>
      <div className="task-actions">
        <button type="submit" className="browser-primary" disabled={working}>
          {working ? "Saving…" : editing ? "Save changes" : "Create task"}
        </button>
        <button
          type="button"
          className="quiet-button"
          disabled={working}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function CheckHistory({ token, task }: { token: string; task: RecurringTask }) {
  const [checks, setChecks] = useState<TaskCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    taskHistory(token, task.id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setChecks(value);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError("Could not load check history.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, task.id, task.last_status, task.last_run_at, retry]);
  return (
    <section
      className="task-history"
      aria-label={`Check history for ${task.title}`}
    >
      <h4>Check history</h4>
      <p className="task-note">{task.prompt}</p>
      <p>
        <strong>Notify when:</strong> {task.notification_condition}
      </p>
      {loading ? (
        <p role="status">Loading checks…</p>
      ) : error ? (
        <p role="alert">
          {error}{" "}
          <button
            className="quiet-button"
            onClick={() => setRetry((value) => value + 1)}
          >
            Retry
          </button>
        </p>
      ) : checks.length === 0 ? (
        <p>
          No checks yet. Use Check now or wait for the next scheduled check.
        </p>
      ) : (
        <ol className="task-check-list">
          {checks.map((check) => (
            <li key={check.run_id}>
              <div className="task-row-heading">
                <strong>
                  {taskOutcome(
                    ["running", "queued"].includes(check.status)
                      ? check.status
                      : check.outcome,
                  )}
                </strong>
                <time dateTime={check.created_at}>
                  {dateLabel(check.created_at)}
                </time>
              </div>
              {check.note && <p>{check.note}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function RecurringTasks({
  token,
  researchReady,
  onClose,
  onChanged,
  onOpenChat,
}: {
  token: string;
  researchReady: boolean;
  onClose: () => void;
  onChanged: () => void;
  onOpenChat: (id: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tasksTab = useRef<HTMLButtonElement>(null);
  const lock = useRef(false);
  const sequence = useRef(0);
  const alive = useRef(true);
  const editorHeading = useRef<HTMLDivElement>(null);
  const [tasks, setTasks] = useState<RecurringTask[]>([]);
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [inbox, setInbox] = useState<TaskInbox>({ unread_count: 0, items: [] });
  const [view, setView] = useState<"tasks" | "updates" | "telegram">("tasks");
  const [telegramLinked, setTelegramLinked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [editor, setEditor] = useState<{
    id?: string;
    input: TaskInput;
  } | null>(null);
  const [historyId, setHistoryId] = useState("");
  const [deleting, setDeleting] = useState("");

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const current = ++sequence.current;
      const [saved, updates, examples] = await Promise.all([
        listTasks(token, signal),
        taskInbox(token, signal),
        taskTemplates(token, signal),
      ]);
      if (!signal?.aborted && alive.current && current === sequence.current) {
        setTasks(saved);
        setInbox(updates);
        setTemplates(examples);
        setLoading(false);
        setLoadError("");
      }
    },
    [token],
  );

  useEffect(() => {
    const element = dialog.current!;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    alive.current = true;
    element.showModal();
    const controller = new AbortController();
    const update = () => {
      if (lock.current) return;
      void refresh(controller.signal).catch(() => {
        if (!controller.signal.aborted) {
          setLoadError(
            "Could not refresh tasks and updates. Retry when your connection is back.",
          );
          setLoading(false);
        }
      });
    };
    update();
    const timer = window.setInterval(update, 10000);
    return () => {
      alive.current = false;
      ++sequence.current;
      controller.abort();
      window.clearInterval(timer);
      element.close();
      previous?.focus();
    };
  }, [refresh]);

  useEffect(() => {
    if (editor) editorHeading.current?.scrollIntoView({ block: "start" });
  }, [editor]);

  async function act(operation: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true;
    ++sequence.current;
    setWorking(true);
    setError("");
    setNotice("");
    try {
      const message = await operation();
      if (!alive.current) return;
      setNotice(message);
      setDeleting("");
      onChanged();
      await refresh();
    } catch (cause) {
      if (alive.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not complete this action. Refresh and try again.",
        );
    } finally {
      lock.current = false;
      if (alive.current) setWorking(false);
    }
  }

  function edit(input: TaskInput, id?: string) {
    setEditor({ id, input });
    setError("");
    setNotice("");
  }

  return (
    <dialog
      ref={dialog}
      className="saved-logins-dialog tasks-dialog"
      aria-labelledby="tasks-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header className="saved-logins-header">
        <h2 id="tasks-title">Recurring tasks</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="Close recurring tasks"
          autoFocus
        >
          <Icon name="close" />
        </button>
      </header>
      <div className="saved-logins-body tasks-body">
        {!editor && (
          <nav className="task-views" aria-label="Recurring task views">
            <button
              ref={tasksTab}
              className="quiet-button"
              aria-pressed={view === "tasks"}
              onClick={() => setView("tasks")}
            >
              Tasks
            </button>
            <button
              className="quiet-button"
              aria-pressed={view === "updates"}
              onClick={() => setView("updates")}
            >
              Updates{inbox.unread_count > 0 ? ` (${inbox.unread_count})` : ""}
            </button>
            <button
              className="quiet-button"
              aria-pressed={view === "telegram"}
              onClick={() => {
                setView("telegram");
                setNotice("");
              }}
            >
              Telegram
            </button>
          </nav>
        )}
        {(error || loadError) && (
          <div className="task-error" role="alert">
            <p>{error || loadError}</p>
            <button
              className="quiet-button"
              disabled={working}
              onClick={() =>
                void act(async () => "Tasks and updates refreshed.")
              }
            >
              Refresh
            </button>
          </div>
        )}
        {notice && (
          <p className="task-notice" role="status">
            {notice}
          </p>
        )}
        {!researchReady && view !== "telegram" && (
          <p className="task-warning">
            Web research needs Gemini and Exa configured on Guuey’s worker. You
            can save a task now; checks will report an error until research is
            available.
          </p>
        )}
        <TelegramSettings
          token={token}
          hidden={view !== "telegram" || !!editor}
          onLinkedChange={setTelegramLinked}
          onChooseTasks={() => {
            setView("tasks");
            setNotice("");
            tasksTab.current?.focus();
          }}
          onChanged={() => {
            onChanged();
            void refresh().catch(() =>
              setLoadError("Could not refresh tasks. Try again."),
            );
          }}
        />
        {view === "telegram" && !editor ? null : loading ? (
          <p role="status">Loading tasks and updates…</p>
        ) : editor ? (
          <div ref={editorHeading}>
            <TaskForm
              key={editor.id ?? "new"}
              initial={editor.input}
              editing={!!editor.id}
              working={working}
              telegramLinked={telegramLinked}
              onCancel={() => setEditor(null)}
              onSave={(input) =>
                void act(async () => {
                  const saved = await saveTask(token, input, editor.id);
                  setEditor(null);
                  return saved.enabled
                    ? `Saved ${saved.title}. Next check: ${dateLabel(saved.next_run_at)}.`
                    : `Saved ${saved.title} as paused.`;
                })
              }
            />
          </div>
        ) : view === "tasks" ? (
          <>
            <div className="task-toolbar">
              <p>Set a schedule and choose what deserves an update.</p>
              <button
                className="browser-primary"
                disabled={working}
                onClick={() => edit(blankTask())}
              >
                New task
              </button>
            </div>
            {tasks.length === 0 && (
              <div className="task-empty">
                <h3>No recurring tasks yet</h3>
                <p>
                  Guuey can check for new information while you’re away and save
                  matching results here.
                </p>
              </div>
            )}
            {templates.map((template) => (
              <button
                key={template.id}
                className="quiet-button task-example"
                disabled={working}
                onClick={() => {
                  const { id: _id, ...input } = template;
                  edit({ ...blankTask(), ...input });
                }}
              >
                Use hourly CVE example
              </button>
            ))}
            <ul className="task-list">
              {tasks.map((task) => {
                const active =
                  task.last_status === "queued" ||
                  task.last_status === "running";
                return (
                  <li key={task.id}>
                    <div className="task-row-heading">
                      <h3>{task.title}</h3>
                      <span className="task-state">
                        {task.enabled
                          ? taskFrequency(task.interval_minutes)
                          : "Paused"}
                      </span>
                    </div>
                    <p className="task-condition">
                      {task.notification_condition}
                    </p>
                    <p className="task-note">
                      {task.enabled ? (
                        <>
                          Next check{" "}
                          <time dateTime={task.next_run_at}>
                            {dateLabel(task.next_run_at)}
                          </time>
                        </>
                      ) : (
                        `${taskFrequency(task.interval_minutes)} when resumed`
                      )}
                      {" · "}
                      {taskOutcome(task.last_status)}
                      {task.telegram_enabled && " · Telegram on"}
                    </p>
                    {task.last_error && (
                      <p className="task-warning">{task.last_error}</p>
                    )}
                    <div className="task-actions">
                      <button
                        className="quiet-button"
                        disabled={working || active}
                        aria-label={
                          active ? taskOutcome(task.last_status) : undefined
                        }
                        onClick={() =>
                          void act(async () => {
                            await checkTask(token, task.id);
                            return `Queued a check for ${task.title}.`;
                          })
                        }
                      >
                        {active && (
                          <TaskActivity
                            label={`${task.title}: ${taskOutcome(task.last_status)}`}
                          />
                        )}
                        {active ? taskOutcome(task.last_status) : "Check now"}
                      </button>
                      <button
                        className="quiet-button"
                        disabled={working}
                        onClick={() =>
                          void act(async () => {
                            const saved = await enableTask(
                              token,
                              task.id,
                              !task.enabled,
                            );
                            return saved.enabled
                              ? `Resumed ${task.title}. Next check: ${dateLabel(saved.next_run_at)}.`
                              : `Paused ${task.title}. Any queued or running check will finish.`;
                          })
                        }
                      >
                        {task.enabled ? "Pause" : "Resume"}
                      </button>
                      <button
                        className="quiet-button"
                        disabled={working || active}
                        onClick={() =>
                          edit(
                            {
                              title: task.title,
                              prompt: task.prompt,
                              notification_condition:
                                task.notification_condition,
                              interval_minutes: task.interval_minutes,
                              timezone: task.timezone,
                              enabled: task.enabled,
                              telegram_enabled: task.telegram_enabled,
                            },
                            task.id,
                          )
                        }
                      >
                        Edit
                      </button>
                      <button
                        className="quiet-button"
                        aria-expanded={historyId === task.id}
                        aria-controls={`task-history-${task.id}`}
                        onClick={() =>
                          setHistoryId(historyId === task.id ? "" : task.id)
                        }
                      >
                        History
                      </button>
                      <button
                        className="quiet-button"
                        disabled={working}
                        onClick={() =>
                          void act(async () => {
                            await onOpenChat(task.chat_id);
                            onClose();
                            return "";
                          })
                        }
                      >
                        Open chat
                      </button>
                      <button
                        className="quiet-button"
                        disabled={working || active}
                        onClick={() => setDeleting(task.id)}
                      >
                        Delete
                      </button>
                    </div>
                    {deleting === task.id && (
                      <div className="task-delete">
                        <p>
                          Delete {task.title} and its updates? Saved chat
                          messages will remain.
                        </p>
                        <div className="task-actions">
                          <button
                            className="browser-danger"
                            disabled={working}
                            onClick={() =>
                              void act(async () => {
                                await deleteTask(token, task.id);
                                return `Deleted ${task.title}.`;
                              })
                            }
                          >
                            Delete task
                          </button>
                          <button
                            className="quiet-button"
                            disabled={working}
                            onClick={() => setDeleting("")}
                          >
                            Keep task
                          </button>
                        </div>
                      </div>
                    )}
                    {historyId === task.id && (
                      <div id={`task-history-${task.id}`}>
                        <CheckHistory token={token} task={task} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="task-note">
              Updates are saved in Guuey. Link Telegram to also receive matches
              from the tasks you choose. Pausing stops future checks; a queued
              or running check will finish.
            </p>
          </>
        ) : (
          <>
            <div className="task-toolbar">
              <p>New matches from your recurring checks.</p>
              <button
                className="quiet-button"
                disabled={working || inbox.unread_count === 0}
                onClick={() =>
                  void act(async () => {
                    await readAllTaskUpdates(token);
                    return "All updates marked as read.";
                  })
                }
              >
                Mark all as read
              </button>
            </div>
            {inbox.items.length === 0 && (
              <div className="task-empty">
                <h3>No updates yet</h3>
                <p>
                  When a check finds something that matches your condition, it
                  will appear here with its sources.
                </p>
              </div>
            )}
            <ul className="task-list task-updates">
              {inbox.items.map((update) => (
                <li key={update.id}>
                  <div className="task-row-heading">
                    <h3>
                      {update.title}
                      {!update.read_at && (
                        <span className="task-unread">Unread</span>
                      )}
                    </h3>
                    <time dateTime={update.created_at}>
                      {dateLabel(update.created_at)}
                    </time>
                  </div>
                  <p className="task-update-content">{update.content}</p>
                  {update.sources.length > 0 && (
                    <ul className="task-sources" aria-label="Sources">
                      {update.sources
                        .filter((source) => /^https?:\/\//i.test(source.url))
                        .map((source) => (
                          <li key={source.url}>
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {source.title || source.url}
                            </a>
                          </li>
                        ))}
                    </ul>
                  )}
                  <div className="task-actions">
                    <button
                      className="quiet-button"
                      disabled={working}
                      onClick={() =>
                        void act(async () => {
                          await onOpenChat(update.chat_id);
                          await readTaskUpdate(token, update.id);
                          onClose();
                          return "";
                        })
                      }
                    >
                      Open chat
                    </button>
                    {!update.read_at && (
                      <button
                        className="quiet-button"
                        disabled={working}
                        onClick={() =>
                          void act(async () => {
                            await readTaskUpdate(token, update.id);
                            return "Update marked as read.";
                          })
                        }
                      >
                        Mark as read
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </dialog>
  );
}
