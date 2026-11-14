import type { ChatSummary } from "../chatApi";
import { Icon } from "./Icon";

type Props = {
  onCollapse: () => void;
  onDisconnect?: () => void;
  onPin: (id: string, pinned: boolean) => void;
  runningChatId?: string;
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
  onPin,
  runningChatId,
  chats,
  activeId,
  query,
  onQueryChange,
  onSelect,
  onNewChat,
  disabled = false,
}: Props) {
  const groups = query
    ? [{ label: "Search results", chats }]
    : [
        { label: "Pinned", chats: chats.filter((chat) => chat.pinned) },
        { label: "Recents", chats: chats.filter((chat) => !chat.pinned) },
      ];
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
        {groups.map((group) => (
          <section
            className="chat-group"
            key={group.label}
            aria-label={group.label}
          >
            <h2 className="sidebar-section-title">{group.label}</h2>
            {group.chats.map((chat) => (
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
                    <span
                      className="activity-spinner"
                      role="status"
                      aria-label="Task running"
                    />
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
            {!group.chats.length && (
              <p className="chat-list-empty">
                {query
                  ? "No chats found."
                  : group.label === "Pinned"
                    ? "Pin a chat to keep it here."
                    : "Your chats will appear here."}
              </p>
            )}
          </section>
        ))}
      </nav>
      {onDisconnect && (
        <button
          className="sidebar-disconnect"
          type="button"
          onClick={onDisconnect}
          disabled={disabled}
        >
          <Icon name="logout" />
          Disconnect
        </button>
      )}
    </aside>
  );
}
