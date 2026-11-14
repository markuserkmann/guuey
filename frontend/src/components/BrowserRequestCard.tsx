import { useEffect, useId, useRef, type FormEvent } from "react";
import { liveViewUrl, type BrowserRequest } from "../browserApi";
import type { ResearchRun } from "../chatApi";
import { useBrowserRequest } from "../useBrowserRequest";

export function BrowserRequestCard({
  token,
  request,
  liveView,
  onRun,
  onCancel,
}: {
  token: string;
  request: BrowserRequest;
  liveView: string | null;
  onRun: (run: ResearchRun) => void;
  onCancel: () => Promise<void>;
}) {
  const id = useId();
  const form = useRef<HTMLFormElement>(null);
  const state = useBrowserRequest(token, request, onRun);
  const disabled = state.expired || state.sending || state.sent;
  const view = liveViewUrl(liveView);
  useEffect(() => {
    if (state.expired || state.sent) form.current?.reset();
  }, [state.expired, state.sent]);

  function sendInputs(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.kind !== "input" || disabled) return;
    const data = new FormData(event.currentTarget);
    const values = Object.fromEntries(
      request.payload.fields.map((field) => [
        field.id,
        String(data.get(field.id) ?? ""),
      ]),
    );
    event.currentTarget.reset();
    void state.answer({ values });
  }

  return (
    <section className="browser-request" aria-labelledby={`${id}-title`}>
      <header>
        <p className="browser-site">{request.site}</p>
        <h2 id={`${id}-title`}>
          {request.kind === "approval"
            ? "Review before submitting"
            : request.kind === "input"
              ? "Your information is needed"
              : "Finish this step in the browser"}
        </h2>
      </header>
      {state.sent ? (
        <p role="status">Response sent. Waiting for the browser to continue.</p>
      ) : state.expired ? (
        <p role="status">
          This request has expired. Stop this task and start a new one when
          you’re ready.
        </p>
      ) : (
        <>
          {request.kind === "approval" && (
            <>
              <p className="browser-request-summary">
                {request.payload.summary}
              </p>
              <p className="browser-note">
                Website button: <strong>{request.payload.button_label}</strong>
              </p>
              {request.has_screenshot && (
                <div className="browser-screenshot">
                  {state.screenshot ? (
                    <img
                      src={state.screenshot}
                      alt="Website at the time this approval was requested"
                      width="1280"
                      height="720"
                    />
                  ) : state.screenshotError ? (
                    <div>
                      <p>Could not load the page image.</p>
                      <button
                        type="button"
                        className="quiet-button"
                        onClick={state.retryScreenshot}
                      >
                        Retry image
                      </button>
                    </div>
                  ) : (
                    <p role="status">Loading page image…</p>
                  )}
                </div>
              )}
              <p className="browser-note">
                Approve only after checking the action and total. The browser
                will submit this action once.
              </p>
              <div className="browser-buttons">
                <button
                  className="quiet-button"
                  disabled={disabled}
                  onClick={() => void state.answer({ decision: "decline" })}
                >
                  Decline
                </button>
                <button
                  className="browser-primary"
                  disabled={disabled}
                  onClick={() => void state.answer({ decision: "approve" })}
                >
                  Approve action
                </button>
              </div>
            </>
          )}
          {request.kind === "input" && (
            <form ref={form} onSubmit={sendInputs} autoComplete="off">
              <p>{request.payload.reason}</p>
              <fieldset disabled={disabled} className="browser-fields">
                <legend className="sr-only">
                  Information for {request.site}
                </legend>
                {request.payload.fields.map((field) => (
                  <label key={field.id} htmlFor={`${id}-${field.id}`}>
                    {field.label}
                    {field.kind === "choice" ? (
                      <select
                        id={`${id}-${field.id}`}
                        name={field.id}
                        required
                        defaultValue=""
                      >
                        <option value="" disabled>
                          Choose an option
                        </option>
                        {field.choices.map((choice) => (
                          <option key={choice} value={choice}>
                            {choice}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id={`${id}-${field.id}`}
                        name={field.id}
                        required
                        maxLength={12000}
                        type={
                          ["password", "otp", "magic_link"].includes(field.kind)
                            ? "password"
                            : field.kind === "email"
                              ? "email"
                              : "text"
                        }
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                      />
                    )}
                  </label>
                ))}
              </fieldset>
              <p className="browser-note">
                Passwords, one-time codes and sign-in links stay out of the
                conversation and model responses. Other answers are shared with
                the assistant.
              </p>
              <div className="browser-buttons">
                <button
                  type="button"
                  className="quiet-button"
                  disabled={disabled}
                  onClick={() => {
                    form.current?.reset();
                    void onCancel();
                  }}
                >
                  Cancel task
                </button>
                <button
                  type="submit"
                  className="browser-primary"
                  disabled={disabled}
                >
                  Send information
                </button>
              </div>
            </form>
          )}
          {request.kind === "takeover" && (
            <>
              <p>{request.payload.reason}</p>
              <p className="browser-note">
                Complete the step in the browser, then return here.
              </p>
              <div className="browser-buttons">
                {view ? (
                  <a
                    className="quiet-button"
                    href={view}
                    target="_blank"
                    rel="noopener noreferrer"
                    referrerPolicy="no-referrer"
                  >
                    Open live view
                  </a>
                ) : (
                  <p className="browser-note">
                    Use the browser window on the machine running this task.
                  </p>
                )}
                <button
                  className="browser-primary"
                  disabled={disabled}
                  onClick={() => void state.answer({ done: true })}
                >
                  I’m done
                </button>
              </div>
            </>
          )}
          <p className="browser-expiry">
            {state.sending
              ? "Sending response…"
              : `Expires in ${Math.floor(state.remaining / 60)}:${String(state.remaining % 60).padStart(2, "0")}`}
          </p>
        </>
      )}
      {state.error && (
        <p className="error-message" role="alert">
          {state.error}
        </p>
      )}
    </section>
  );
}
