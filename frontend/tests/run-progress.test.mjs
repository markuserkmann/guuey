import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { loadSource } from "./load-source.mjs";

const api = await loadSource(
  [
    "./src/runPresentation.ts",
    "./src/runStream.ts",
    "./src/components/RunActivity.tsx",
  ],
  true,
);
const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});
const run = {
  id: "run",
  chat_id: "chat",
  request_id: "request",
  message: "Check availability",
  created_at: new Date().toISOString(),
  mode: "auto",
  status: "running",
  phase: "planning",
  result: null,
  live_output: null,
  debug: {},
};

test("progress stays separate from the answer and uses one stable message", () => {
  const progress = {
    ...run,
    live_output: { kind: "progress", text: "Checking **availability**." },
  };
  const messages = api.runMessages([], progress);
  assert.equal(messages.length, 2);
  assert.equal(messages[1].content, "");
  assert.equal(messages[1].delivery, "updating");
  const streamed = api.runMessages(messages, {
    ...run,
    live_output: { kind: "draft", text: "First findings" },
  });
  assert.equal(streamed.length, 2);
  assert.equal(streamed[1].id, messages[1].id);
  assert.equal(streamed[1].content, "First findings");
  assert.equal(streamed[1].draft, true);
});

test("browser handoffs retain the answer and clearly say it will be updated", () => {
  const handoff = {
    ...run,
    phase: "browser_starting",
    result: { reply: "Initial answer", sources: [] },
  };
  const items = api.runMessages([], handoff);
  assert.equal(items[1].content, "Initial answer");
  assert.equal(items[1].delivery, "updating");
  const status = api.runPresentation(handoff);
  assert.equal(status.title, "Switching to browser…");
  assert.match(status.detail, /answer will update here/);
  assert.equal(status.state, "working");
});

test("final results replace an existing answer without duplicates or stale drafts", () => {
  const source = { title: "Availability", url: "https://example.com" };
  const old = [
    { id: "user:request", role: "user", content: run.message },
    {
      id: "assistant:run",
      role: "assistant",
      content: "Old answer",
      draft: true,
      delivery: "updating",
    },
  ];
  const updated = api.runMessages(old, {
    ...run,
    status: "completed",
    result: { reply: "Checked answer", sources: [source] },
    live_output: { kind: "draft", text: "Outdated stream" },
  });
  assert.equal(updated.length, 2);
  assert.equal(updated[1].content, "Checked answer");
  assert.equal(updated[1].draft, false);
  assert.equal(updated[1].delivery, undefined);
  assert.deepEqual(updated[1].sources, [source]);
  assert.equal(old[1].content, "Old answer");
});

test("continuations do not duplicate the user's request", () => {
  const messages = [
    { id: "previous-user", role: "user", content: run.message },
  ];
  const updated = api.runMessages(messages, { ...run, parent_id: "parent" });
  assert.equal(updated.filter((item) => item.role === "user").length, 1);
});

test("waiting, reconnecting, partial, failed and cancelled runs never claim completion", () => {
  for (const phase of [
    "awaiting_input",
    "awaiting_approval",
    "awaiting_takeover",
  ])
    assert.equal(api.runPresentation({ ...run, phase }).state, "waiting");
  assert.equal(api.runPresentation(run, true).state, "reconnecting");
  for (const status of ["partial", "failed", "cancelled"])
    assert.equal(api.runPresentation({ ...run, status }).state, "incomplete");
  assert.equal(
    api.runPresentation({ ...run, status: "completed" }, true).state,
    "complete",
  );
  assert.equal(
    api.runPresentation({ ...run, phase: "answer_ready" }).state,
    "working",
  );
  assert.equal(
    api.runPresentation({ ...run, phase: "internal_new_phase" }).title,
    "Working on your request…",
  );
});

test("only active work shows a spinner; completion and pauses have distinct symbols", () => {
  for (const [variant, spinner, stop] of [
    [run, true, true],
    [{ ...run, phase: "awaiting_input" }, false, true],
    [{ ...run, status: "completed" }, false, false],
    [{ ...run, status: "cancelled" }, false, false],
  ]) {
    const html = api.renderToStaticMarkup(
      api.createElement(api.RunActivity, {
        run: variant,
        reconnecting: false,
        onStop: async () => {},
      }),
    );
    assert.equal(html.includes('class="activity-spinner"'), spinner);
    assert.equal(html.includes(">Stop</button>"), stop);
    assert.match(html, /role="status" aria-live="polite" aria-atomic="true"/);
  }
});

async function receive(text, chunkSize = 1) {
  const bytes = new TextEncoder().encode(text);
  globalThis.fetch = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (let offset = 0; offset < bytes.length; offset += chunkSize)
            controller.enqueue(bytes.slice(offset, offset + chunkSize));
          controller.close();
        },
      }),
    );
  const received = [];
  await api.streamRun(
    "a".repeat(40),
    "run",
    new AbortController().signal,
    (snapshot) => received.push(snapshot),
  );
  return received;
}

test("SSE parses split CRLF, bare CR, UTF-8, no-space fields and multiline data", async () => {
  const snapshot = {
    ...run,
    live_output: { kind: "answer", text: "Hinnad € • Tallinn" },
  };
  for (const newline of ["\r\n", "\r", "\n"]) {
    const json = JSON.stringify(snapshot, null, 2)
      .split("\n")
      .map((line) => `data:${line}`)
      .join(newline);
    const stream = `: keepalive${newline}${newline}event:run${newline}${json}${newline}${newline}`;
    assert.deepEqual(await receive(stream), [snapshot]);
  }
});

test("SSE ignores unrelated events and runs, and discards incomplete frames", async () => {
  const stream = `event: log\ndata: not JSON\n\nevent: run\ndata: ${JSON.stringify({ ...run, id: "other" })}\n\nevent: run\ndata: ${JSON.stringify(run)}\n\nevent: run\ndata: {"id":"run"`;
  assert.deepEqual(await receive(stream, 17), [run]);
});

test("invalid updates reject for reconnect instead of corrupting displayed content", async () => {
  for (const snapshot of [
    { ...run, status: "unknown" },
    { ...run, live_output: { kind: "answer", text: { bad: "value" } } },
    { ...run, result: { reply: null, sources: [] } },
  ])
    await assert.rejects(
      receive(`event: run\ndata: ${JSON.stringify(snapshot)}\n\n`),
      /Invalid live update/,
    );
});

test("a terminal snapshot finishes immediately even if the connection stays open", async () => {
  let cancelled = false;
  globalThis.fetch = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              `event: run\ndata: ${JSON.stringify({ ...run, status: "completed" })}\n\n`,
            ),
          );
        },
        cancel() {
          cancelled = true;
        },
      }),
    );
  const received = [];
  await api.streamRun(
    "a".repeat(40),
    "run",
    new AbortController().signal,
    (value) => received.push(value),
  );
  assert.equal(received[0].status, "completed");
  assert.equal(cancelled, true);
});
