export function tokenError(token: string): string | null {
  if (token.length < 32)
    return "Enter the backend token. It must be at least 32 characters.";
  if (!/^[\x21-\x7E]+$/.test(token)) {
    return "The backend token must contain only ASCII characters without spaces. Use the AGENT_API_TOKEN value from your backend configuration; enter your request in the chat after connecting.";
  }
  return null;
}
export const DEV_SESSION = "local-development-proxy";

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
