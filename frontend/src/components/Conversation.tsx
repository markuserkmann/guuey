import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useAuiState,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import type { ChatMessage } from "../chatApi";
import { Message } from "./Message";
import { Icon } from "./Icon";

function RuntimeMessage({ onRetry }: { onRetry: () => void }) {
  const message = useAuiState(
    (state) => state.message.metadata.custom.record as ChatMessage | undefined,
  );
  if (!message) return null;
  return <Message message={message} onRetry={onRetry} />;
}

export function Conversation({
  messages,
  busy,
  onSend,
  onRetry,
  onCancel,
  children,
}: {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (content: string) => Promise<void>;
  onRetry: () => void;
  onCancel: () => Promise<void>;
  children: ReactNode;
}) {
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: busy,
    convertMessage: (message): ThreadMessageLike => ({
      id: message.id,
      role: message.role,
      content: [{ type: "text", text: message.content }],
      metadata: { custom: { record: message } },
      ...(message.role === "assistant"
        ? {
            status:
              message.delivery === "updating"
                ? { type: "running" as const }
                : { type: "complete" as const, reason: "stop" as const },
          }
        : {}),
    }),
    onNew: async (message) => {
      await onSend(
        message.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("\n"),
      );
    },
    onCancel,
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="conversation-root">
        <ThreadPrimitive.Viewport
          className="conversation"
          autoScroll
          aria-label="Conversation"
        >
          <ThreadPrimitive.Messages>
            {() => <RuntimeMessage onRetry={onRetry} />}
          </ThreadPrimitive.Messages>
          {children}
        </ThreadPrimitive.Viewport>
        <ThreadPrimitive.ScrollToBottom
          className="scroll-latest icon-button"
          aria-label="Scroll to latest message"
          behavior="smooth"
        >
          <Icon name="down" />
        </ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
