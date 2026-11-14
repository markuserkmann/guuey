export async function exportCookies(api, clipboard) {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  let url;
  try {
    url = new URL(tab?.url);
  } catch {
    throw new Error("Open an HTTPS website before exporting cookies.");
  }
  if (url.protocol !== "https:" || url.username || url.password)
    throw new Error("Open an HTTPS website before exporting cookies.");
  const permissions = { origins: [`${url.origin}/*`] };
  if (!(await api.permissions.request(permissions)))
    throw new Error("Site access was declined. No cookies were exported.");
  try {
    const [current, root] = await Promise.all([
      api.cookies.getAll({ url: url.href }),
      api.cookies.getAll({ url: `${url.origin}/` }),
    ]);
    const unique = new Map();
    for (const cookie of [...current, ...root]) {
      if (cookie.partitionKey) continue;
      const domain = cookie.domain.replace(/^\./, "");
      if (url.hostname !== domain && !url.hostname.endsWith(`.${domain}`))
        continue;
      const key = JSON.stringify([cookie.name, cookie.domain, cookie.path]);
      unique.set(key, {
        name: cookie.name,
        value: cookie.value,
        domain: cookie.domain,
        path: cookie.path,
        expires: cookie.session ? -1 : cookie.expirationDate,
        httpOnly: cookie.httpOnly,
        secure: cookie.secure,
        sameSite: cookie.sameSite,
        hostOnly: cookie.hostOnly,
      });
    }
    if (!unique.size)
      throw new Error(
        "No supported cookies were found. Sign in to the website first.",
      );
    if (unique.size > 500)
      throw new Error(
        "This site has more than 500 cookies. Export a smaller set with your browser’s cookie tools.",
      );
    const text = JSON.stringify({
      format: "guuey-cookies",
      version: 1,
      origin: url.origin,
      cookies: [...unique.values()],
    });
    if (new TextEncoder().encode(text).length > 1_048_576)
      throw new Error("The cookie export exceeds 1 MiB.");
    await clipboard.writeText(text);
    return { count: unique.size, site: url.hostname };
  } finally {
    const removed = await api.permissions.remove(permissions);
    if (!removed)
      throw new Error(
        "Site access could not be removed. Remove this helper in chrome://extensions after exporting.",
      );
  }
}
