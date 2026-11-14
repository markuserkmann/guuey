import { useEffect, useRef, useState } from "react";
import {
  answerBrowserRequest,
  browserScreenshot,
  type BrowserAnswer,
  type BrowserRequest,
} from "./browserApi";
import { getRun, type ResearchRun } from "./chatApi";

export function useBrowserRequest(
  token: string,
  request: BrowserRequest,
  onRun: (run: ResearchRun) => void,
) {
  const [now, setNow] = useState(Date.now);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [screenshotError, setScreenshotError] = useState(false);
  const [imageAttempt, setImageAttempt] = useState(0);
  const lock = useRef(false);
  const mounted = useRef(true);
  const remaining = Math.max(
    0,
    Math.ceil((Date.parse(request.expires_at) - now) / 1000),
  );
  const expired =
    !Number.isFinite(remaining) ||
    remaining === 0 ||
    request.status !== "pending";

  useEffect(() => {
    mounted.current = true;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!request.has_screenshot || expired || sent) return;
    let current = true;
    let objectUrl: string | null = null;
    const controller = new AbortController();
    setScreenshot(null);
    setScreenshotError(false);
    void browserScreenshot(token, request.run_id, request.id, controller.signal)
      .then((blob) => {
        if (!current) return;
        objectUrl = URL.createObjectURL(blob);
        setScreenshot(objectUrl);
      })
      .catch(() => {
        if (current) setScreenshotError(true);
      });
    return () => {
      current = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [
    token,
    request.run_id,
    request.id,
    request.has_screenshot,
    expired,
    sent,
    imageAttempt,
  ]);

  async function answer(value: BrowserAnswer) {
    if (
      lock.current ||
      expired ||
      sent ||
      Date.now() >= Date.parse(request.expires_at)
    )
      return;
    lock.current = true;
    setSending(true);
    setError("");
    try {
      await answerBrowserRequest(token, request.run_id, request.id, value);
      if (mounted.current) setSent(true);
    } catch {
      if (mounted.current)
        setError(
          "The response could not be confirmed. Checking the saved request. Private values must be entered again to retry.",
        );
    } finally {
      try {
        const updated = await getRun(token, request.run_id);
        if (mounted.current) {
          onRun(updated);
          if (
            updated.browser?.request?.id !== request.id ||
            updated.status !== "running"
          )
            setSent(true);
        }
      } catch {
        if (mounted.current)
          setError(
            "Could not refresh the browser task. Check your connection before responding again.",
          );
      }
      lock.current = false;
      if (mounted.current) setSending(false);
    }
  }

  return {
    remaining,
    expired,
    sending,
    sent,
    error,
    screenshot,
    screenshotError,
    answer,
    retryScreenshot: () => setImageAttempt((value) => value + 1),
  };
}
