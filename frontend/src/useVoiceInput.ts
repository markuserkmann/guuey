import { useEffect, useRef, useState } from "react";
import { VoiceTranscription } from "./voiceTranscription";

type VoiceState = "idle" | "connecting" | "recording" | "stopping";

function appendText(existing: string, addition: string) {
  if (!existing || !addition) return existing + addition;
  return (
    existing +
    (/\s$/.test(existing) || /^\s|^[,.;:!?]/.test(addition) ? "" : " ") +
    addition
  );
}

export function useVoiceInput({
  token,
  draft,
  onDraftChange,
  disabled,
}: {
  token: string;
  draft: string;
  onDraftChange: (value: string) => void;
  disabled: boolean;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState("");
  const session = useRef<VoiceTranscription | null>(null);
  const finishing = useRef(false);
  const onChange = useRef(onDraftChange);
  onChange.current = onDraftChange;

  useEffect(() => {
    if (disabled) {
      const current = session.current;
      session.current = null;
      finishing.current = false;
      current?.cancel();
      setState("idle");
    }
  }, [disabled]);

  useEffect(
    () => () => {
      const current = session.current;
      session.current = null;
      finishing.current = false;
      current?.cancel();
    },
    [token],
  );

  function cancelPending() {
    if (!finishing.current) return;
    const current = session.current;
    session.current = null;
    finishing.current = false;
    current?.cancel();
    setState("idle");
  }

  function start() {
    if (disabled) return;
    cancelPending();
    if (session.current) return;
    if (draft.length >= 12000) {
      setError("The message is full. Shorten it before adding voice input.");
      return;
    }
    setError("");
    setState("connecting");
    const existing = draft;
    const current = new VoiceTranscription(token, {
      onReady: () => {
        if (session.current === current) setState("recording");
      },
      onStopping: () => {
        if (session.current === current) {
          finishing.current = true;
          setState("stopping");
        }
      },
      onTranscript: (text) => {
        if (session.current !== current) return;
        const next = appendText(existing, text);
        onChange.current(next.slice(0, 12000));
        if (next.length >= 12000) {
          session.current = null;
          finishing.current = false;
          current.cancel();
          setState("idle");
          setError(
            "Voice input stopped at the 12,000-character message limit. Please review the draft.",
          );
        }
      },
      onEnd: (message) => {
        if (session.current !== current) return;
        session.current = null;
        finishing.current = false;
        setState("idle");
        if (message) setError(message);
      },
    });
    session.current = current;
    void current.start();
  }

  return {
    state,
    error,
    active: state === "connecting" || state === "recording",
    finalizing: state === "stopping",
    start,
    stop: () => session.current?.stop(),
    cancelPending,
    changeDraft: (value: string) => {
      cancelPending();
      onChange.current(value);
    },
  };
}
