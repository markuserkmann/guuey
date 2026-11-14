import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import type { ResearchRun } from "../chatApi";
import {
  FILE_ACCEPT,
  FILE_HELP,
  readAttachments,
  attachmentSize,
  formatFileSize,
  type TextAttachment,
} from "../attachments";
import { Icon } from "./Icon";

type Props = {
  draft: string;
  onDraftChange: (value: string) => void;
  attachments: TextAttachment[];
  onAttachmentsChange: (files: TextAttachment[]) => void;
  mode: ResearchRun["mode"];
  onModeChange: (mode: ResearchRun["mode"]) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  disabled: boolean;
  busy: boolean;
  deepAvailable: boolean;
};

export function Composer({
  draft,
  onDraftChange,
  attachments,
  onAttachmentsChange,
  mode,
  onModeChange,
  onSubmit,
  disabled,
  busy,
  deepAvailable,
}: Props) {
  const picker = useRef<HTMLInputElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const reading = useRef(false);
  const mounted = useRef(true);
  const [readingFiles, setReadingFiles] = useState(false);
  const [fileError, setFileError] = useState("");
  const [dragging, setDragging] = useState(false);
  const locked = disabled || readingFiles;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 160)}px`;
  }, [draft]);

  async function addFiles(files: File[]) {
    if (disabled || reading.current || !files.length) return;
    reading.current = true;
    setReadingFiles(true);
    setFileError("");
    try {
      const next = await readAttachments(files, attachments);
      if (mounted.current) onAttachmentsChange(next);
    } catch (cause) {
      if (mounted.current)
        setFileError(
          cause instanceof Error
            ? cause.message
            : "Couldn't read the selected files.",
        );
    } finally {
      reading.current = false;
      if (mounted.current) setReadingFiles(false);
    }
  }

  return (
    <div className="composer-area">
      <form
        className={`composer${dragging ? " dragging" : ""}`}
        onSubmit={(event) => {
          if (locked) event.preventDefault();
          else onSubmit(event);
        }}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
            setDragging(!locked);
          }
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setDragging(false);
        }}
        onDrop={(event) => {
          if (event.dataTransfer.files.length) {
            event.preventDefault();
            setDragging(false);
            void addFiles(Array.from(event.dataTransfer.files));
          }
        }}
      >
        {attachments.length > 0 && (
          <ul className="attachment-list" aria-label="Attached files">
            {attachments.map((file) => (
              <li className="attachment-chip" key={file.name}>
                <Icon name="file" />
                <span className="attachment-label">
                  <span title={file.name}>{file.name}</span>
                  <small>{formatFileSize(attachmentSize(file))}</small>
                </span>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Remove ${file.name}`}
                  disabled={locked}
                  onClick={() => {
                    onAttachmentsChange(
                      attachments.filter((item) => item !== file),
                    );
                    setFileError("");
                  }}
                >
                  <Icon name="close" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <label className="sr-only" htmlFor="message-input">
          Message
        </label>
        <textarea
          ref={input}
          id="message-input"
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          onPaste={(event) => {
            if (event.clipboardData.files.length) {
              event.preventDefault();
              void addFiles(Array.from(event.clipboardData.files));
            }
          }}
          placeholder={
            busy
              ? "Waiting for the current task…"
              : attachments.length
                ? "What would you like to do with these files?"
                : "Describe a new task…"
          }
          rows={1}
          disabled={disabled}
          maxLength={12000}
        />
        <div className="composer-toolbar">
          <div className="composer-tools">
            <input
              ref={picker}
              className="sr-only"
              type="file"
              tabIndex={-1}
              accept={FILE_ACCEPT}
              multiple
              aria-label="Choose files"
              disabled={locked}
              onChange={(event) => {
                void addFiles(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
            />
            <button
              className="icon-button"
              type="button"
              disabled={locked || attachments.length >= 3}
              title={FILE_HELP}
              aria-label="Attach files"
              aria-describedby="file-help"
              onClick={() => picker.current?.click()}
            >
              <Icon name="attach" />
            </button>
            <label className="mode-control">
              <span className="sr-only">Research mode</span>
              <select
                value={mode}
                disabled={disabled}
                onChange={(event) =>
                  onModeChange(event.target.value as ResearchRun["mode"])
                }
              >
                <option value="auto">Auto</option>
                <option value="fast">Fast</option>
                <option value="deep" disabled={!deepAvailable}>
                  Deep
                </option>
              </select>
              <Icon name="chevron" />
            </label>
            {readingFiles && (
              <span className="file-status" role="status">
                Reading files…
              </span>
            )}
          </div>
          <button
            className="send-button"
            type="submit"
            disabled={locked || !draft.trim()}
            aria-label="Send message"
          >
            <Icon name="send" />
          </button>
        </div>
      </form>
      <p
        id="file-help"
        className={
          attachments.length || dragging || fileError
            ? "composer-hint"
            : "sr-only"
        }
      >
        {dragging ? "Drop files to attach" : FILE_HELP}
      </p>
      {fileError && (
        <p className="attachment-error" role="alert">
          {fileError}
        </p>
      )}
    </div>
  );
}
