import { apiFetch, type ResearchRun } from "./chatApi";

export type BrowserStep = {
  seq: number;
  at: string;
  tool: string;
  label: string;
  status: "ok" | "error" | "waiting";
  url: string | null;
  error_code: string | null;
};

export type BrowserField = {
  id: string;
  label: string;
  kind: "text" | "email" | "choice" | "password" | "otp" | "magic_link";
  choices: string[];
};

type RequestBase = {
  id: string;
  run_id: string;
  site: string;
  status: "pending" | "answered" | "taken" | "expired";
  created_at: string;
  expires_at: string;
  has_screenshot: boolean;
};

export type BrowserRequest = RequestBase &
  (
    | { kind: "input"; payload: { reason: string; fields: BrowserField[] } }
    | {
        kind: "approval";
        payload: {
          summary: string;
          button_label: string;
          url: string;
          site: string;
        };
      }
    | { kind: "takeover"; payload: { reason: string } }
  );

export type BrowserAction = {
  id: string;
  summary: string;
  status:
    | "attempting"
    | "approved"
    | "submitted"
    | "verified"
    | "unverified"
    | "declined"
    | "expired"
    | "not_completed";
};
export type UnresolvedAction = BrowserAction & {
  run_id: string;
  site: string;
  created_at: string;
};
export type BrowserState = {
  steps: BrowserStep[];
  request: BrowserRequest | null;
  actions: BrowserAction[];
  live_view_url: string | null;
};
export type SavedLogin = {
  id: string;
  site: string;
  status: "saved" | "unverified" | "expired";
  cookie_count: number;
  revision: number;
  updated_at: string;
};
export type BrowserAnswer =
  | { decision: "approve" | "decline" }
  | { values: Record<string, string> }
  | { done: true };
export type CookieImport = SavedLogin & {
  accepted: number;
  rejected: { name: string; reason: string }[];
};

async function browserJson<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const response = await apiFetch(path, token, init, true);
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

const requestPath = (runId: string, requestId: string) =>
  `/api/runs/${encodeURIComponent(runId)}/browser/requests/${encodeURIComponent(requestId)}`;

export const answerBrowserRequest = (
  token: string,
  runId: string,
  requestId: string,
  answer: BrowserAnswer,
) =>
  browserJson<BrowserRequest>(requestPath(runId, requestId), token, {
    method: "POST",
    body: JSON.stringify(answer),
  });

export async function browserScreenshot(
  token: string,
  runId: string,
  requestId: string,
  signal: AbortSignal,
) {
  const response = await apiFetch(
    requestPath(runId, requestId) + "/screenshot",
    token,
    { signal },
    true,
  );
  return response.blob();
}

export const listSavedLogins = (token: string, signal?: AbortSignal) =>
  browserJson<SavedLogin[]>("/api/browser/sessions", token, { signal });
export const importCookies = (
  token: string,
  site_url: string,
  cookies: string,
) =>
  browserJson<CookieImport>("/api/browser/sessions/import", token, {
    method: "POST",
    body: JSON.stringify({ site_url, cookies }),
  });
export const deleteSavedLogin = (token: string, id: string) =>
  browserJson<void>(`/api/browser/sessions/${encodeURIComponent(id)}`, token, {
    method: "DELETE",
  });
export const listUnresolvedActions = (token: string, signal?: AbortSignal) =>
  browserJson<UnresolvedAction[]>("/api/browser/actions", token, { signal });
export const resolveBrowserAction = (
  token: string,
  id: string,
  decision: "confirmed" | "not_completed",
  evidence: string,
) =>
  browserJson<void>(
    `/api/browser/actions/${encodeURIComponent(id)}/resolve`,
    token,
    { method: "POST", body: JSON.stringify({ decision, evidence }) },
  );

export function isBrowserRun(run: ResearchRun | null) {
  return (
    !!run &&
    (!!run.browser ||
      run.phase.startsWith("browser_") ||
      run.phase.startsWith("awaiting_") ||
      run.phase === "submission_unknown" ||
      run.debug.decision?.workflow === "browser")
  );
}

export function liveViewUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.origin === "https://hb.oxylabs.io" &&
      url.pathname === "/novnc/" &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export function siteLabel(value: string | null): string {
  try {
    return value ? new URL(value).hostname : "";
  } catch {
    return "";
  }
}
