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
    </article>
  );
}
