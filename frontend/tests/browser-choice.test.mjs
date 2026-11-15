import assert from "node:assert/strict";
import { test } from "node:test";
import { loadSource } from "./load-source.mjs";

const ui = await loadSource(
  ["./src/components/BrowserChoice.tsx", "./src/runPresentation.ts"],
  true,
);
const run = {
  id: "search",
  chat_id: "chat",
  request_id: "request",
  message: "Find moving boxes and tape under €40",
  mode: "auto",
  status: "partial",
  phase: "awaiting_browser_choice",
  result: {
    reply:
      "Search findings. Do you want the AI to check the websites in a browser? This will take longer.",
    sources: [{ title: "Store", url: "https://example.com" }],
    next_action: { kind: "browser_opt_in" },
  },
  debug: {},
};
const props = {
  run,
  activeChatId: "chat",
  busy: false,
  enabled: true,
  onChoose() {},
};

test("browser choice exposes two named native controls without starting work", () => {
  const actions = [];
  const element = ui.BrowserChoice({
    ...props,
    onChoose: (choice) => actions.push(choice),
  });
  const markup = ui.renderToStaticMarkup(element);
  assert.match(markup, /role="group" aria-label="Website checking choice"/);
  assert.match(markup, /type="button">Yes, use browser<\/button>/);
  assert.match(markup, /type="button">Keep web results<\/button>/);
  assert.deepEqual(actions, []);

  const [browse, keep] = element.props.children;
  keep.props.onClick();
  assert.deepEqual(actions, [
    { content: "Keep web results", forceBrowser: false },
  ]);
  browse.props.onClick();
  assert.deepEqual(actions, [
    { content: "Keep web results", forceBrowser: false },
    {
      content: "Yes, use the browser to finish the previous search.",
      forceBrowser: true,
    },
  ]);
});

test("choices are hidden during work, after the offer resolves and outside the current chat", () => {
  for (const overrides of [
    { run: null },
    { activeChatId: "another-chat" },
    { busy: true },
    { enabled: false },
    { run: { ...run, status: "running" } },
    { run: { ...run, status: "completed" } },
    { run: { ...run, result: null } },
    { run: { ...run, result: { ...run.result, next_action: undefined } } },
    {
      run: {
        ...run,
        result: { ...run.result, next_action: { kind: "clarification" } },
      },
    },
  ])
    assert.equal(ui.BrowserChoice({ ...props, ...overrides }), null);
});

test("offering browser checks preserves web results and reports stopped work", () => {
  const messages = ui.runMessages([], run);
  assert.equal(messages[1].content, run.result.reply);
  assert.deepEqual(messages[1].sources, run.result.sources);
  assert.equal(messages[1].delivery, undefined);
  const status = ui.runPresentation(run);
  assert.equal(status.title, "Browser check available");
  assert.match(status.detail, /only if you choose it/);
  assert.equal(status.state, "incomplete");
});

test("browser offers without search evidence do not claim search completion", () => {
  const status = ui.runPresentation({
    ...run,
    result: {
      ...run.result,
      reply: "Do you want the AI to check the websites in a browser?",
      sources: [],
    },
  });
  assert.equal(status.title, "Browser check available");
  assert.match(status.detail, /will take longer/);
  assert.doesNotMatch(status.detail, /saved|finished|complete/i);
});
