import type { ChatSummary } from "../chatApi";

type Props = {
  onCollapse: () => void;
  onDisconnect: () => void;
  chats: ChatSummary[];
  activeId: string;
  query: string;
  onQueryChange: (query: string) => void;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  disabled?: boolean;
};

export function ChatSidebar({ onCollapse, onDisconnect, chats, activeId, query, onQueryChange, onSelect, onNewChat, disabled = false }: Props) {
  return (
    <aside className="chat-sidebar" aria-label="Chat navigation">
      <div className="sidebar-heading"><a className="sidebar-brand" href="/">Guuey</a><button className="quiet-button sidebar-close" type="button" onClick={onCollapse} aria-label="Close sidebar">☰</button></div>
      <button className="new-chat-button" type="button" onClick={onNewChat} disabled={disabled}>
        <span aria-hidden="true">＋</span><span>New chat</span>
      </button>
      <div className="sidebar-search">
        <label className="sr-only" htmlFor="chat-search">Search chats</label>
        <input id="chat-search" type="search" value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search chats" />
      </div>
      <div className="sidebar-section-title"><span>Recent chats</span><span>{chats.length}</span></div>
      <nav className="chat-list" aria-label="Saved chats">
        {chats.map((chat) => (
          <button key={chat.id} type="button" className={`chat-list-item${activeId === chat.id ? " active" : ""}`} disabled={disabled}
            aria-current={activeId === chat.id ? "page" : undefined} onClick={() => onSelect(chat.id)} title={chat.title}>
            <span className="chat-list-title">{chat.title}</span>
          </button>
        ))}
        {chats.length === 0 && <p className="chat-list-empty">{query ? "No chats match this search." : "Your saved chats will appear here."}</p>}
      </nav>
      <button className="sidebar-disconnect" type="button" onClick={onDisconnect} disabled={disabled}>Disconnect</button>
    </aside>
  );
}
