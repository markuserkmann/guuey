import {
  useEffect,
  useId,
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
import { useVoiceInput } from "../useVoiceInput";

type Props = {
  token: string;
  draft: string;
  onDraftChange: (value: string) => void;
  attachments: TextAttachment[];
  onAttachmentsChange: (files: TextAttachment[]) => void;
  mode: ResearchRun["mode"];
  onModeChange: (mode: ResearchRun["mode"]) => void;
  forceBrowser: boolean;
  onForceBrowserChange: (forced: boolean) => void;
  browserAvailable: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  disabled: boolean;
  busy: boolean;
  deepAvailable: boolean;
};

export function Composer({
  token,
  draft,
  onDraftChange,
  attachments,
  onAttachmentsChange,
  mode,
  onModeChange,
  forceBrowser,
  onForceBrowserChange,
  browserAvailable,
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
  const [optionsOpen, setOptionsOpen] = useState(false);
  const options = useRef<HTMLDivElement>(null);
  const optionsButton = useRef<HTMLButtonElement>(null);
  const optionsId = useId();
  const locked = disabled || readingFiles;
  const voice = useVoiceInput({ token, draft, onDraftChange, disabled });
  const voiceLabel =
    voice.state === "connecting"
      ? "Cancel microphone connection"
      : voice.state === "recording"
        ? "Stop voice input"
        : voice.finalizing
          ? "Finishing voice input"
          : "Start voice input";

  function stopVoice() {
    voice.stop();
    input.current?.focus();
    input.current?.setSelectionRange(draft.length, draft.length);
  }

  useEffect(() => {
    if (!voice.active) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") stopVoice();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [voice.active, voice.stop]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!optionsOpen) return;
    function dismiss(event: PointerEvent) {
      if (!options.current?.contains(event.target as Node))
        setOptionsOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOptionsOpen(false);
        optionsButton.current?.focus();
      }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [optionsOpen]);

  useEffect(() => {
    if (disabled) setOptionsOpen(false);
  }, [disabled]);

  function resizeInput() {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 160)}px`;
  }

  useLayoutEffect(resizeInput, [draft, busy, attachments.length]);

  useEffect(() => {
    if (!input.current) return;
    let width = input.current.clientWidth;
    const observer = new ResizeObserver(() => {
      if (!input.current || input.current.clientWidth === width) return;
      width = input.current.clientWidth;
      resizeInput();
    });
    observer.observe(input.current);
    return () => observer.disconnect();
  }, []);

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
          if (locked || voice.active || voice.finalizing)
            event.preventDefault();
          else {
            voice.cancelPending();
            onSubmit(event);
          }
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
          onChange={(event) => voice.changeDraft(event.target.value)}
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
              ? "Working on your request…"
              : attachments.length
                ? "What would you like to do with these files?"
                : "Describe a new task…"
          }
          rows={1}
          disabled={disabled}
          readOnly={voice.active}
          aria-describedby={voice.active ? "voice-status" : undefined}
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
            <div
              ref={options}
              className={`composer-options${optionsOpen ? " open" : ""}`}
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget))
                  setOptionsOpen(false);
              }}
            >
              <button
                ref={optionsButton}
                className={`icon-button composer-options-toggle${forceBrowser ? " has-selection" : ""}`}
                type="button"
                aria-label={
                  forceBrowser
                    ? "Message options, browser forced"
                    : "Message options"
                }
                aria-expanded={optionsOpen}
                aria-controls={optionsId}
                disabled={disabled}
                onClick={() => setOptionsOpen((open) => !open)}
              >
                <Icon name="settings" />
              </button>
              <div className="composer-options-panel" id={optionsId}>
                <label
                  className="browser-control"
                  title={
                    browserAvailable
                      ? "Force browser use for debugging. When unchecked, routing and browser handoffs are automatic."
                      : "Browser interaction is unavailable on this server."
                  }
                >
                  <input
                    type="checkbox"
                    checked={forceBrowser}
                    onChange={(event) =>
                      onForceBrowserChange(event.target.checked)
                    }
                    disabled={disabled || !browserAvailable}
                  />
                  Force browser
                </label>
                <p className="composer-options-help">
                  {browserAvailable
                    ? "Open websites directly for this request."
                    : "Browser interaction is unavailable on this server."}
                </p>
              </div>
            </div>
          </div>
          <div className="composer-actions">
            <button
              className={`icon-button voice-button${voice.active ? " voice-active" : ""}${voice.state === "recording" ? " voice-listening" : ""}`}
              type="button"
              disabled={
                disabled || voice.finalizing || (!voice.active && readingFiles)
              }
              aria-label={voiceLabel}
              aria-pressed={voice.active}
              title={voiceLabel}
              onClick={() => {
                if (voice.active) stopVoice();
                else voice.start();
              }}
            >
              <Icon name={voice.active ? "stop" : "microphone"} />
            </button>
            <button
              className="send-button"
              type="submit"
              disabled={
                locked || voice.active || voice.finalizing || !draft.trim()
              }
              aria-label="Send message"
            >
              <Icon name="send" />
            </button>
          </div>
        </div>
        {readingFiles && (
          <p className="file-status" role="status">
            Reading files…
          </p>
        )}
        <p id="voice-status" className="voice-status" role="status">
          {voice.state === "connecting"
            ? "Connecting microphone… Allow access if prompted."
            : voice.state === "recording"
              ? "Listening… Stop when you’re ready to edit."
              : voice.state === "stopping"
                ? "Microphone off. Finishing transcription…"
                : ""}
        </p>
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
      {voice.error && (
        <p className="attachment-error" role="alert">
          {voice.error}
        </p>
      )}
    </div>
  );
}
