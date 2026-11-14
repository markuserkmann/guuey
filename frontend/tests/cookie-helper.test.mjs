import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { exportCookies } from "../cookie-helper/export.js";
import { unzipSync, strFromU8 } from "fflate";

const cookie = {
  name: "sid",
  value: "private-session",
  domain: ".example.com",
  path: "/",
  session: true,
  httpOnly: true,
  secure: true,
  sameSite: "lax",
  hostOnly: false,
};
function fixture({
  url = "https://shop.example.com/account",
  granted = true,
  cookies = [cookie],
  failClipboard = false,
} = {}) {
  const calls = [];
  let text;
  return {
    calls,
    get text() {
      return text;
    },
    api: {
      tabs: { query: async () => [{ url }] },
      permissions: {
        request: async (value) => {
          calls.push(["request", value]);
          return granted;
        },
        remove: async (value) => {
          calls.push(["remove", value]);
          return true;
        },
      },
      cookies: {
        getAll: async (value) => {
          calls.push(["cookies", value]);
          return cookies;
        },
      },
    },
    clipboard: {
      writeText: async (value) => {
        if (failClipboard) throw new Error("Clipboard unavailable");
        text = value;
      },
    },
  };
}

test("export merges cookie paths, filters partitioned cookies and removes site access", async () => {
  const state = fixture({
    cookies: [
      cookie,
      { ...cookie, path: "/account" },
      { ...cookie, name: "partitioned", partitionKey: {} },
      { ...cookie, domain: "other.com" },
    ],
  });
  const result = await exportCookies(state.api, state.clipboard);
  assert.equal(result.count, 2);
  const exported = JSON.parse(state.text);
  assert.equal(exported.origin, "https://shop.example.com");
  assert.equal(exported.format, "guuey-cookies");
  assert.equal(exported.version, 1);
  assert.equal(exported.cookies[0].expires, -1);
  assert.equal(exported.cookies[0].value, "private-session");
  assert.deepEqual(state.calls.at(-1), [
    "remove",
    { origins: ["https://shop.example.com/*"] },
  ]);
});

test("clipboard failures still remove temporary host permission", async () => {
  const state = fixture({ failClipboard: true });
  await assert.rejects(
    exportCookies(state.api, state.clipboard),
    /Clipboard unavailable/,
  );
  assert.equal(state.calls.at(-1)[0], "remove");
});

test("failed permission removal is reported instead of claiming cleanup", async () => {
  const state = fixture();
  state.api.permissions.remove = async () => false;
  await assert.rejects(
    exportCookies(state.api, state.clipboard),
    /Site access could not be removed/,
  );
});

test("unsupported pages and declined access never read cookies", async () => {
  for (const options of [
    { url: "chrome://settings" },
    { url: "http://example.com" },
    { url: "https://user:secret@example.com" },
    { granted: false },
  ]) {
    const state = fixture(options);
    await assert.rejects(exportCookies(state.api, state.clipboard));
    assert.ok(!state.calls.some(([kind]) => kind === "cookies"));
    assert.equal(state.text, undefined);
  }
});

test("empty and oversized exports are rejected and permission is removed", async () => {
  for (const cookies of [
    [],
    Array.from({ length: 501 }, (_, i) => ({ ...cookie, name: `cookie${i}` })),
    [{ ...cookie, value: "x".repeat(1_048_576) }],
  ]) {
    const state = fixture({ cookies });
    await assert.rejects(exportCookies(state.api, state.clipboard));
    assert.equal(state.calls.at(-1)[0], "remove");
    assert.equal(state.text, undefined);
  }
});

test("packaged extension has only popup resources and no network permission", async () => {
  await import("../scripts/package-cookie-helper.mjs");
  const archive = unzipSync(
    await readFile(new URL("../public/cookie-helper.zip", import.meta.url)),
  );
  assert.deepEqual(Object.keys(archive).sort(), [
    "export.js",
    "manifest.json",
    "popup.css",
    "popup.html",
    "popup.js",
  ]);
  const manifest = JSON.parse(strFromU8(archive["manifest.json"]));
  assert.match(
    manifest.content_security_policy.extension_pages,
    /connect-src 'none'/,
  );
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.host_permissions, undefined);
  assert.deepEqual(manifest.optional_host_permissions, ["https://*/*"]);
});
