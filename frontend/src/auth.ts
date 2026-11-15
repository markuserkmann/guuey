export function tokenError(token: string): string | null {
  if (token.length < 32)
    return "Enter the backend token. It must be at least 32 characters.";
  if (!/^[\x21-\x7E]+$/.test(token)) {
    return "The backend token must contain only ASCII characters without spaces. Use the AGENT_API_TOKEN value from your backend configuration; enter your request in the chat after connecting.";
  }
  return null;
}
export const DEV_SESSION = "local-development-proxy";
const COOKIE_SESSION = "user-session:";
export type SignedInUser = { id: string; username: string };
export type AppSession = { token: string; user?: SignedInUser };

export function authHeaders(token: string): Record<string, string> {
  if (token.startsWith(COOKIE_SESSION))
    return {
      "X-Guuey-Request": "1",
      "X-Guuey-User": token.slice(COOKIE_SESSION.length),
    };
  if (token === DEV_SESSION) return { "X-Guuey-Dev-Session": "1" };
  const error = tokenError(token);
  if (error) throw new Error(error);
  return { Authorization: `Bearer ${token}` };
}

export function sessionExpired(token: string, status: number) {
  if (status === 401 && token.startsWith(COOKIE_SESSION))
    window.dispatchEvent(new Event("guuey-session-expired"));
}

function userSession(user: SignedInUser): AppSession {
  return { token: COOKIE_SESSION + user.id, user };
}

async function authRequest(path: string, body: object): Promise<Response> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Guuey-Request": "1" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "Could not sign in. Try again.",
    );
  }
  return response;
}

function notifyAccountChange() {
  try {
    localStorage.setItem("guuey-account-change", crypto.randomUUID());
  } catch {
    // Account-bound API requests still reject stale tabs when storage is blocked.
  }
}

export async function signIn(
  username: string,
  password: string,
): Promise<AppSession> {
  const response = await authRequest("/api/auth/login", { username, password });
  const data = await response.json();
  notifyAccountChange();
  return userSession(data.user);
}

export async function signInWithMagicLink(value: string): Promise<AppSession> {
  let token = value.trim();
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    try {
      const url = new URL(token);
      token = new URLSearchParams(url.hash.slice(1)).get("magic") ?? "";
    } catch {
      token = "";
    }
  }
  if (!/^[A-Za-z0-9_-]{43}$/.test(token))
    throw new Error("Enter the complete magic link you were given.");
  const response = await authRequest("/api/auth/magic-link", { token });
  const data = await response.json();
  notifyAccountChange();
  return userSession(data.user);
}

export async function signOut(): Promise<void> {
  await authRequest("/api/auth/logout", {});
  notifyAccountChange();
}

export async function restoreSession(): Promise<AppSession | null> {
  const url = new URL(window.location.href);
  const fragment = new URLSearchParams(url.hash.slice(1));
  if (fragment.has("magic")) {
    const magic = fragment.get("magic") ?? "";
    // Fragments stay out of HTTP logs; remove the credential before making requests.
    url.hash = "";
    window.history.replaceState(null, "", url);
    return signInWithMagicLink(magic);
  }
  const response = await fetch("/api/session", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (response.status === 401 || response.status === 503) return null;
  if (!response.ok)
    throw new Error("Could not restore your session. Sign in again.");
  const data = await response.json();
  return data.user ? userSession(data.user) : null;
}

export async function hasDevSession(): Promise<boolean> {
  if (!import.meta.env.DEV) return false;
  try {
    const response = await fetch("/__dev/session", {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    return response.ok && (await response.json()).enabled === true;
  } catch {
    return false;
  }
}
