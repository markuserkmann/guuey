import Markdown from "react-markdown";
import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { MessagePrimitive } from "@assistant-ui/react";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../chatApi";
import { Icon } from "./Icon";
import { attachmentSize, formatFileSize } from "../attachments";

function confirmExternalNavigation(
  event: MouseEvent<HTMLAnchorElement>,
  requestNavigation: (url: string, source: HTMLAnchorElement) => void,
) {
  const link = event.currentTarget;
  if (!link.href || new URL(link.href).origin === window.location.origin)
    return;

  event.preventDefault();
  requestNavigation(link.href, link);
}

export function Message({
  message,
  onRetry,
  activity,
}: {
  message: ChatMessage;
  onRetry?: () => void;
  activity?: ReactNode;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">(
    "idle",
  );
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const returnFocusRef = useRef<HTMLAnchorElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const openRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!pendingUrl) return;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPendingUrl(null);
      if (event.key === "Tab") {
        const first = cancelRef.current;
        const last = openRef.current;
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      returnFocusRef.current?.focus();
    };
  }, [pendingUrl]);

  function requestNavigation(url: string, source: HTMLAnchorElement) {
    returnFocusRef.current = source;
    setPendingUrl(url);
  }

  function openPendingLink() {
    if (!pendingUrl) return;
    window.open(pendingUrl, "_blank", "noopener,noreferrer");
    setPendingUrl(null);
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }
  return (
    <MessagePrimitive.Root asChild>
      <article className={`message message-${message.role}`}>
        {activity}
        <div
          className="message-content"
          aria-busy={message.delivery === "updating"}
        >
          {message.role === "assistant" ? (
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ node: _node, ...props }) => (
                  <a
                    {...props}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) =>
                      confirmExternalNavigation(event, requestNavigation)
                    }
                  />
                ),
              }}
            >
              {message.content}
            </Markdown>
          ) : (
            message.content
          )}
        </div>
        {!!message.attachments?.length && (
          <div className="message-attachments" aria-label="Message attachments">
            {message.attachments.map((file) => (
              <details className="message-file" key={file.name}>
                <summary>
                  <Icon name="file" />
                  <span>{file.name}</span>
                  <small>{formatFileSize(attachmentSize(file))}</small>
                </summary>
                <pre>{file.content}</pre>
              </details>
            ))}
          </div>
        )}
        {message.role === "assistant" && message.sources?.length ? (
          <details className="message-sources">
            <summary>Sources ({message.sources.length})</summary>
            <ul>
              {message.sources.map((source) => (
                <li key={source.url}>
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) =>
                      confirmExternalNavigation(event, requestNavigation)
                    }
                  >
                    {source.title}
                  </a>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {pendingUrl && (
          <div
            className="link-check-backdrop"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setPendingUrl(null);
            }}
          >
            <section
              className="link-check-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="link-check-title"
              aria-describedby="link-check-description"
            >
              <h2 id="link-check-title">Open this link?</h2>
              <p id="link-check-description">
                This will take you to a new tab. Check the destination before
                you continue.
              </p>
              <code className="link-check-url">{pendingUrl}</code>
              <div className="link-check-actions">
                <button
                  ref={cancelRef}
                  className="quiet-button"
                  type="button"
                  onClick={() => setPendingUrl(null)}
                >
                  Cancel
                </button>
                <button
                  ref={openRef}
                  className="link-check-open"
                  type="button"
                  onClick={openPendingLink}
                >
                  Open in new tab
                </button>
              </div>
            </section>
          </div>
        )}
        {message.content && (
          <div className="message-actions">
            {message.delivery && message.role === "user" && (
              <span
                className={`delivery-status ${message.delivery}`}
                role="status"
              >
                {message.delivery === "sending"
                  ? "Sending…"
                  : message.delivery === "failed"
                    ? "Could not send"
                    : "Sent"}
              </span>
            )}
            {message.delivery === "failed" && (
              <button type="button" className="quiet-button" onClick={onRetry}>
                Retry
              </button>
            )}
            <button
              type="button"
              className="icon-button copy-message"
              aria-label={
                copyState === "copied" ? "Message copied" : "Copy message"
              }
              title={copyState === "copied" ? "Copied" : "Copy message"}
              onClick={() => void copy()}
              onBlur={() => setCopyState("idle")}
            >
              <Icon name={copyState === "copied" ? "check" : "copy"} />
            </button>
            {copyState === "error" && (
              <span role="alert">
                Couldn't copy. Select the text to copy it.
              </span>
            )}
          </div>
        )}
      </article>
    </MessagePrimitive.Root>
  );
}
