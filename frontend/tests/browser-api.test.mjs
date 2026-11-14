import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { loadSource } from "./load-source.mjs";

const api = await loadSource([
  "./src/browserApi.ts",
  "./src/chatApi.ts",
  "./src/runStream.ts",
]);
const token = "a".repeat(40);
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("browser responses carry auth and private values only in the request body", async () => {
  let captured;
  globalThis.fetch = async (path, options) => {
    captured = { path, ...options };
    return Response.json({ id: "request", status: "answered" });
  };
  await api.answerBrowserRequest(token, "run/1", "request/1", {
    values: { password: "private-value" },
  });
  assert.equal(captured.path, "/api/runs/run%2F1/browser/requests/request%2F1");
  assert.equal(captured.headers.Authorization, `Bearer ${token}`);
  assert.equal(captured.cache, "no-store");
  assert.equal(JSON.parse(captured.body).values.password, "private-value");
  assert.ok(!captured.path.includes("private-value"));
});

test("private failures never echo server input or malformed validation detail", async () => {
  for (const status of [401, 404, 409, 422, 503]) {
    globalThis.fetch = async () =>
      Response.json(
        { detail: [{ msg: "private-value", input: "private-value" }] },
        { status },
      );
    await assert.rejects(
      api.importCookies(token, "https://example.com", "sid=private-value"),
      (error) =>
        error.status === status && !error.message.includes("private-value"),
    );
  }
});

test("deletion and action resolution accept empty 204 responses", async () => {
  const requests = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, ...options });
    return new Response(null, { status: 204 });
  };
  assert.equal(await api.deleteSavedLogin(token, "saved/id"), undefined);
  assert.equal(
    await api.resolveBrowserAction(
      token,
      "action/id",
      "not_completed",
      "No order in account",
    ),
    undefined,
  );
  assert.equal(requests[0].method, "DELETE");
  assert.equal(requests[1].path, "/api/browser/actions/action%2Fid/resolve");
  assert.deepEqual(JSON.parse(requests[1].body), {
    decision: "not_completed",
    evidence: "No order in account",
  });
});

test("approval screenshots use authenticated non-cacheable fetches and abort signals", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/api/runs/run/browser/requests/request/screenshot");
    assert.equal(options.headers.Authorization, `Bearer ${token}`);
    assert.equal(options.signal, controller.signal);
    assert.equal(options.cache, "no-store");
    return new Response("jpeg", { headers: { "Content-Type": "image/jpeg" } });
  };
  const blob = await api.browserScreenshot(
    token,
    "run",
    "request",
    controller.signal,
  );
  assert.equal(blob.type, "image/jpeg");
});

test("development sessions do not send a fabricated bearer token", async () => {
  globalThis.fetch = async (_, options) => {
    assert.equal(options.headers.Authorization, undefined);
    return Response.json([]);
  };
  await api.listSavedLogins("local-development-proxy");
});

test("live view links reject other origins, credentials and unsupported protocols", () => {
  for (const url of [
    "javascript:alert(1)",
    "https://hb.oxylabs.io.evil.test/novnc/",
    "https://user@hb.oxylabs.io/novnc/",
    "http://hb.oxylabs.io/novnc/",
    "https://hb.oxylabs.io/elsewhere",
    "bad",
  ])
    assert.equal(api.liveViewUrl(url), null);
  assert.equal(
    api.liveViewUrl("https://hb.oxylabs.io/novnc/?id=session"),
    "https://hb.oxylabs.io/novnc/?id=session",
  );
});

test("browser runs stay separate from research continuation and retries", () => {
  for (const phase of [
    "browser_done",
    "browser_unavailable",
    "awaiting_input",
    "submission_unknown",
  ])
    assert.ok(api.isBrowserRun({ phase, debug: {} }));
  assert.ok(
    api.isBrowserRun({
      phase: "budget_exhausted",
      debug: {},
      browser: { steps: [] },
    }),
  );
  assert.equal(api.isBrowserRun({ phase: "finished", debug: {} }), false);
  assert.equal(
    api.runProgress({ phase: "awaiting_approval", debug: {} }),
    "Waiting for your approval",
  );
});

test("fragmented SSE frames deliver browser requests without mixing runs", async () => {
  const snapshot = {
    id: "run",
    status: "running",
    phase: "awaiting_input",
    browser: {
      request: { id: "request" },
      steps: [],
      actions: [],
      live_view_url: null,
    },
  };
  const data = `event: run\ndata: ${JSON.stringify({ ...snapshot, id: "other" })}\n\nevent: run\ndata: ${JSON.stringify(snapshot)}\n\n`;
  const encoder = new TextEncoder();
  globalThis.fetch = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (let i = 0; i < data.length; i += 7)
            controller.enqueue(encoder.encode(data.slice(i, i + 7)));
          controller.close();
        },
      }),
    );
  const received = [];
  await api.streamRun(token, "run", new AbortController().signal, (value) =>
    received.push(value),
  );
  assert.deepEqual(received, [snapshot]);
});
