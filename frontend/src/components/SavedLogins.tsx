import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  deleteSavedLogin,
  importCookies,
  listSavedLogins,
  listUnresolvedActions,
  resolveBrowserAction,
  type SavedLogin,
  type UnresolvedAction,
} from "../browserApi";
import { Icon } from "./Icon";

export function SavedLogins({
  token,
  enabled,
  onClose,
  onChanged,
}: {
  token: string;
  enabled: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const lock = useRef(false);
  const refreshSequence = useRef(0);
  const [logins, setLogins] = useState<SavedLogin[]>([]);
  const [actions, setActions] = useState<UnresolvedAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [deleting, setDeleting] = useState("");

  async function refresh(signal?: AbortSignal) {
    const sequence = ++refreshSequence.current;
    setLoading(true);
    setError("");
    try {
      const [saved, unresolved] = await Promise.all([
        listSavedLogins(token, signal),
        listUnresolvedActions(token, signal),
      ]);
      if (!signal?.aborted && sequence === refreshSequence.current) {
        setLogins(saved);
        setActions(unresolved);
      }
    } catch {
      if (!signal?.aborted && sequence === refreshSequence.current)
        setError(
          "Could not load saved logins and uncertain actions. Refresh to try again.",
        );
    } finally {
      if (!signal?.aborted && sequence === refreshSequence.current)
        setLoading(false);
    }
  }

  useEffect(() => {
    const element = dialog.current!;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    element.showModal();
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => {
      ++refreshSequence.current;
      controller.abort();
      element.close();
      previous?.focus();
    };
  }, [token]);

  async function mutate(key: string, operation: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true;
    setWorking(key);
    setError("");
    setNotice("");
    try {
      const message = await operation();
      setNotice(message);
      setDeleting("");
      await refresh();
      onChanged();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not complete this action. Refresh to check its status.",
      );
    } finally {
      lock.current = false;
      setWorking("");
    }
  }

  function importLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (lock.current) return;
    const data = new FormData(event.currentTarget);
    const site = String(data.get("site_url") ?? "").trim();
    const cookies = String(data.get("cookies") ?? "");
    const paste = event.currentTarget.elements.namedItem(
      "cookies",
    ) as HTMLTextAreaElement;
    paste.value = "";
    if (new TextEncoder().encode(cookies).length > 1_048_576) {
      setError(
        "Cookie input must be no larger than 1 MiB. Paste a smaller export.",
      );
      return;
    }
    try {
      const url = new URL(site);
      if (url.protocol !== "https:" || url.username || url.password)
        throw new Error();
    } catch {
      setError(
        "Enter an HTTPS site URL without a username or password, then paste the cookies again.",
      );
      return;
    }
    void mutate("import", async () => {
      const result = await importCookies(token, site, cookies);
      return `Imported ${result.accepted} ${result.accepted === 1 ? "cookie" : "cookies"} for ${result.site}. Login is not verified yet.${result.rejected.length ? ` ${result.rejected.length} rejected: ${[...new Set(result.rejected.map((item) => item.reason))].join("; ")}.` : ""}`;
    });
  }

  function resolve(
    event: FormEvent<HTMLFormElement>,
    action: UnresolvedAction,
  ) {
    event.preventDefault();
    if (lock.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const decision = data.get("decision");
    const evidence = String(data.get("evidence") ?? "").trim();
    if (
      !evidence ||
      (decision !== "confirmed" && decision !== "not_completed")
    ) {
      setError(
        "Choose the outcome and describe what you checked in your account.",
      );
      return;
    }
    void mutate(action.id, async () => {
      await resolveBrowserAction(token, action.id, decision, evidence);
      form.reset();
      return "Action reviewed. You can start a new task for this site.";
    });
  }

  return (
    <dialog
      ref={dialog}
      className="saved-logins-dialog"
      aria-labelledby="saved-logins-title"
      onCancel={onClose}
    >
      <header className="saved-logins-header">
        <h2 id="saved-logins-title">Saved logins</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="Close saved logins"
          autoFocus
        >
          <Icon name="close" />
        </button>
      </header>
      <div className="saved-logins-body">
        <p className="browser-note">
          Reuse a website login in later browser tasks. Deleting a login also
          stops any task using it.
        </p>
        <button
          className="quiet-button"
          disabled={loading || !!working}
          onClick={() => void refresh()}
        >
          Refresh list
        </button>
        {loading && <p role="status">Loading saved logins…</p>}
        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="browser-notice" role="status">
            {notice}
          </p>
        )}
        {!loading && !error && logins.length === 0 && (
          <p>
            No saved logins yet. Sign in during a browser task or import cookies
            below.
          </p>
        )}
        <ul className="saved-login-list">
          {logins.map((login) => (
            <li key={login.id}>
              <div className="saved-login-row">
                <div>
                  <strong>{login.site}</strong>
                  <p className="browser-note">
                    {
                      {
                        saved: "Verified",
                        unverified: "Not verified",
                        expired: "Sign-in expired",
                      }[login.status]
                    }{" "}
                    · {login.cookie_count} cookies
                  </p>
                  <small>
                    Updated {new Date(login.updated_at).toLocaleString()}
                  </small>
                </div>
                <button
                  className="quiet-button"
                  disabled={!!working}
                  onClick={() => setDeleting(login.id)}
                  aria-label={`Delete login for ${login.site}`}
                >
                  Delete
                </button>
              </div>
              {deleting === login.id && (
                <div className="login-delete-confirm">
                  <p>
                    Delete the login for {login.site}? Any browser task using it
                    will stop.
                  </p>
                  <div className="browser-buttons">
                    <button
                      className="quiet-button"
                      disabled={!!working}
                      onClick={() => setDeleting("")}
                    >
                      Keep login
                    </button>
                    <button
                      className="browser-danger"
                      disabled={!!working}
                      onClick={() =>
                        void mutate(login.id, async () => {
                          await deleteSavedLogin(token, login.id);
                          return `Deleted login for ${login.site}.`;
                        })
                      }
                    >
                      {working === login.id ? "Deleting…" : "Delete login"}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
        <section
          className="saved-login-import"
          aria-labelledby="import-login-title"
        >
          <h3 id="import-login-title">Import login cookies</h3>
          {enabled ? (
            <form onSubmit={importLogin} autoComplete="off">
              <fieldset className="browser-fields" disabled={!!working}>
                <legend className="sr-only">Import login cookies</legend>
                <label>
                  Website URL
                  <input
                    type="url"
                    name="site_url"
                    required
                    placeholder="https://example.com"
                    maxLength={8192}
                    autoComplete="off"
                  />
                </label>
                <label>
                  Cookies
                  <textarea
                    name="cookies"
                    required
                    rows={5}
                    maxLength={1_048_576}
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    aria-describedby="cookie-formats"
                  />
                </label>
              </fieldset>
              <p id="cookie-formats" className="browser-note">
                Paste helper JSON, cookie-editor JSON, a Netscape file, or a
                Cookie header. The paste box clears when you submit. Cookies are
                stored encrypted and are not added to chat.
              </p>
              <button
                type="submit"
                className="browser-primary"
                disabled={!!working}
              >
                {working === "import" ? "Importing…" : "Import cookies"}
              </button>
            </form>
          ) : (
            <p className="browser-note">
              Importing logins requires a browser session key on the backend.
              Existing logins can still be deleted.
            </p>
          )}
          <details className="cookie-helper-info">
            <summary>Export cookies from your browser</summary>
            <p>
              <a href="/cookie-helper.zip" download>
                Download cookie helper
              </a>
            </p>
            <p>
              Unzip it, open chrome://extensions, enable Developer mode, then
              choose Load unpacked and select the extracted folder. Open the
              website’s tab and use the helper to export its cookies.
            </p>
            <p className="browser-note">
              The helper requests temporary access to that site and puts its
              cookies on your clipboard. Paste them here, then replace the
              clipboard contents.
            </p>
          </details>
        </section>
        <section
          aria-labelledby="uncertain-actions-title"
          className="uncertain-actions"
        >
          <h3 id="uncertain-actions-title">Uncertain actions</h3>
          {!loading && !error && !actions.length && (
            <p className="browser-note">No actions need review.</p>
          )}
          {actions.map((action) => (
            <form
              key={action.id}
              className="uncertain-action"
              onSubmit={(event) => resolve(event, action)}
            >
              <strong>{action.site}</strong>
              <p>{action.summary}</p>
              <p className="browser-warning">
                Check your account on the website first. Only finished tasks can
                be resolved.
              </p>
              <fieldset className="browser-fields" disabled={!!working}>
                <legend className="sr-only">
                  Review action on {action.site}
                </legend>
                <label>
                  What happened?
                  <select name="decision" required defaultValue="">
                    <option value="" disabled>
                      Choose the outcome
                    </option>
                    <option value="confirmed">The action completed</option>
                    <option value="not_completed">
                      The action did not complete
                    </option>
                  </select>
                </label>
                <label>
                  What did you find in your account?
                  <textarea
                    name="evidence"
                    required
                    maxLength={2000}
                    rows={2}
                  />
                </label>
              </fieldset>
              <button
                type="submit"
                className="browser-primary"
                disabled={!!working}
              >
                {working === action.id ? "Saving…" : "Save checked outcome"}
              </button>
            </form>
          ))}
        </section>
      </div>
    </dialog>
  );
}
