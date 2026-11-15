import type { ChatSummary } from "../chatApi";
import { taskFrequency, taskOutcome, type RecurringTask } from "../taskApi";
import { Icon } from "./Icon";
import { TaskActivity } from "./TaskActivity";

type Props = {
  onCollapse: () => void;
  onDisconnect?: () => void;
  username?: string;
  onPin: (id: string, pinned: boolean) => void;
  runningChatId?: string;
  tasks: RecurringTask[];
  tasksLoading: boolean;
  tasksError: string;
  unreadUpdates: number;
  onOpenTasks: () => void;
  onRetryTasks: () => void;
  chats: ChatSummary[];
  activeId: string;
  query: string;
  onQueryChange: (query: string) => void;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  disabled?: boolean;
};

export function ChatSidebar({
  onCollapse,
  onDisconnect,
  username,
  onPin,
  runningChatId,
  tasks,
  tasksLoading,
  tasksError,
  unreadUpdates,
  onOpenTasks,
  onRetryTasks,
  chats,
  activeId,
  query,
  onQueryChange,
  onSelect,
  onNewChat,
  disabled = false,
}: Props) {
  const taskChatIds = new Set(tasks.map((task) => task.chat_id));
  const ordinaryChats = chats.filter((chat) => !taskChatIds.has(chat.id));
  const search = query.trim().toLocaleLowerCase();
  const matchingChatIds = new Set(chats.map((chat) => chat.id));
  const visibleTasks = tasks.filter(
    (task) =>
      !search ||
      matchingChatIds.has(task.chat_id) ||
      `${task.title} ${task.prompt}`.toLocaleLowerCase().includes(search),
  );

  function chatGroup(label: string, items: ChatSummary[]) {
    return (
      <section className="chat-group" aria-label={label}>
        <h2 className="sidebar-section-title">{label}</h2>
        {items.map((chat) => (
          <div
            className={`chat-row${activeId === chat.id ? " active" : ""}`}
            key={chat.id}
          >
            <button
              type="button"
              className="chat-list-item"
              disabled={disabled}
              aria-current={activeId === chat.id ? "page" : undefined}
              onClick={() => onSelect(chat.id)}
              title={chat.title}
            >
              <span className="chat-list-title">{chat.title}</span>
              {runningChatId === chat.id && (
                <TaskActivity label="Task running" />
              )}
            </button>
            <button
              type="button"
              className="pin-chat icon-button"
              aria-label={`${chat.pinned ? "Unpin" : "Pin"} ${chat.title}`}
              aria-pressed={!!chat.pinned}
              title={chat.pinned ? "Unpin chat" : "Pin chat"}
              onClick={() => onPin(chat.id, !chat.pinned)}
            >
              <Icon name="pin" />
            </button>
          </div>
        ))}
        {!items.length && (
          <p className="chat-list-empty">
            {search
              ? "No chats found."
              : label === "Pinned"
                ? "Pin a chat to keep it here."
                : "Your chats will appear here."}
          </p>
        )}
      </section>
    );
  }
  return (
    <aside className="chat-sidebar" aria-label="Chat navigation">
      <div className="sidebar-heading">
        <a className="sidebar-brand" href="/">
          Guuey
        </a>
        <button
          className="icon-button"
          type="button"
          onClick={onCollapse}
          aria-label="Close sidebar"
        >
          <Icon name="sidebar" />
        </button>
      </div>
      <button
        className="new-chat-button"
        type="button"
        onClick={onNewChat}
        disabled={disabled}
      >
        <Icon name="compose" />
        <span>New chat</span>
      </button>
      <div className="sidebar-search">
        <Icon name="search" />
        <label className="sr-only" htmlFor="chat-search">
          Search chats
        </label>
        <input
          id="chat-search"
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search chats"
        />
      </div>
      <nav className="chat-list" aria-label="Saved chats">
        {!search &&
          chatGroup(
            "Pinned",
            ordinaryChats.filter((chat) => chat.pinned),
          )}
        <section className="chat-group" aria-label="Recurring tasks">
          <h2 className="sidebar-section-title sidebar-task-heading">
            <button
              type="button"
              className="sidebar-tasks-button"
              aria-haspopup="dialog"
              aria-label={
                unreadUpdates
                  ? `Recurring tasks, ${unreadUpdates} unread updates`
                  : "Recurring tasks"
              }
              onClick={onOpenTasks}
            >
              <span>Recurring tasks</span>
              {unreadUpdates > 0 && (
                <span className="sidebar-task-count" aria-hidden="true">
                  {unreadUpdates}
                </span>
              )}
              <Icon name="chevron" className="sidebar-task-chevron" />
            </button>
          </h2>
          {tasksLoading && (
            <p className="chat-list-empty" role="status">
              Loading tasks…
            </p>
          )}
          {tasksError && (
            <p className="chat-list-empty sidebar-task-error" role="status">
              {tasksError}
              <button type="button" onClick={onRetryTasks}>
                Retry tasks
              </button>
            </p>
          )}
          {visibleTasks.map((task) => {
            const checking =
              runningChatId === task.chat_id ||
              (!tasksError &&
                ["queued", "running"].includes(task.last_status ?? ""));
            const status = checking
              ? runningChatId === task.chat_id
                ? "Checking"
                : taskOutcome(task.last_status)
              : tasksError
                ? "Status unavailable"
                : !task.enabled
                  ? "Paused"
                  : task.last_status === "failed"
                    ? "Check failed"
                    : taskFrequency(task.interval_minutes);
            return (
              <div
                className={`chat-row${activeId === task.chat_id ? " active" : ""}`}
                key={task.id}
              >
                <button
                  type="button"
                  className="chat-list-item sidebar-task-item"
                  disabled={disabled}
                  aria-label={`${task.title}, ${status}`}
                  aria-current={activeId === task.chat_id ? "page" : undefined}
                  onClick={() => onSelect(task.chat_id)}
                  title={task.title}
                >
                  <span className="sidebar-task-copy">
                    <span className="chat-list-title">{task.title}</span>
                    <span className="sidebar-task-status">{status}</span>
                  </span>
                  {checking && (
                    <TaskActivity label={`${task.title}: ${status}`} />
                  )}
                </button>
              </div>
            );
          })}
          {!tasksLoading && !tasksError && !visibleTasks.length && (
            <p className="chat-list-empty">
              {search ? "No matching tasks." : "No recurring tasks yet."}
            </p>
          )}
          {!tasksLoading && !tasksError && !tasks.length && !search && (
            <button
              className="sidebar-create-task"
              type="button"
              onClick={onOpenTasks}
            >
              Create a recurring task
            </button>
          )}
        </section>
        {chatGroup(
          search ? "Search results" : "Recents",
          ordinaryChats.filter((chat) => search || !chat.pinned),
        )}
      </nav>
      {username && (
        <p className="sidebar-account" title={username}>
          Signed in as <strong>{username}</strong>
        </p>
      )}
      {onDisconnect && (
        <button
          className="sidebar-disconnect"
          type="button"
          onClick={onDisconnect}
        >
          <Icon name="logout" />
          Sign out
        </button>
      )}
    </aside>
  );
}
