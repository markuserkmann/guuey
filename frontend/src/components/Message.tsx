import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../chatApi";

export function Message({ message }: { message: ChatMessage }) {
  return (
    <article className={`message message-${message.role}`}>
      <div className="message-content">
        {message.role === "assistant" ? (
          <Markdown remarkPlugins={[remarkGfm]}>{message.content}</Markdown>
        ) : message.content}
      </div>
      {message.role === "assistant" && message.sources?.length ? (
        <details className="message-sources">
          <summary>Sources ({message.sources.length})</summary>
          <ul>{message.sources.map((source) => (
            <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a></li>
          ))}</ul>
        </details>
      ) : null}
    </article>
  );
}
