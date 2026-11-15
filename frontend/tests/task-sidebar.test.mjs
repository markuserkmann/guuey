import assert from "node:assert/strict";
import { test } from "node:test";
import { loadSource } from "./load-source.mjs";

const ui = await loadSource(["./src/components/ChatSidebar.tsx"], true);
const task = {
  id: "cve-task",
  chat_id: "cve-chat",
  title: "Critical CVE watch",
  prompt: "Check vendor advisories",
  interval_minutes: 60,
  enabled: true,
  last_status: "running",
};
const render = (props = {}) =>
  ui.renderToStaticMarkup(
    ui.createElement(ui.ChatSidebar, {
      chats: [
        { id: "cve-chat", title: task.title, pinned: true },
        { id: "normal", title: "Trip planning", pinned: false },
      ],
      activeId: "normal",
      query: "",
      tasks: [task],
      tasksLoading: false,
      tasksError: "",
      unreadUpdates: 0,
      onCollapse() {},
      onPin() {},
      onOpenTasks() {},
      onRetryTasks() {},
      onQueryChange() {},
      onSelect() {},
      onNewChat() {},
      ...props,
    }),
  );
const section = (markup, label) =>
  markup.match(
    new RegExp(`<section[^>]+aria-label="${label}"[^>]*>(.*?)</section>`),
  )?.[1];

test("recurring chats have their own group even when pinned", () => {
  const markup = render();
  assert.match(section(markup, "Recurring tasks"), /Critical CVE watch/);
  assert.doesNotMatch(section(markup, "Pinned"), /Critical CVE watch/);
  assert.doesNotMatch(section(markup, "Recents"), /Critical CVE watch/);
  assert.match(section(markup, "Recents"), /Trip planning/);
  assert.doesNotMatch(markup, /Unpin Critical CVE watch/);
});

test("background checks expose a running arrow without the task chat selected", () => {
  assert.match(
    render(),
    /class="task-activity" role="status" aria-label="Critical CVE watch: Checking"/,
  );
  assert.doesNotMatch(
    render({ tasks: [{ ...task, last_status: "no_match" }] }),
    /class="task-activity"/,
  );
  assert.match(
    render({ tasks: [{ ...task, enabled: false }] }),
    /class="task-activity"/,
  );
  assert.match(
    render({ tasks: [{ ...task, enabled: false, last_status: "no_match" }] }),
    /Paused/,
  );
});

test("task search works when its chat is outside the saved-chat results", () => {
  const markup = render({ chats: [], query: "vendor" });
  assert.match(section(markup, "Recurring tasks"), /Critical CVE watch/);
  assert.match(render({ chats: [], query: "unrelated" }), /No matching tasks/);
});

test("task navigation distinguishes unread, loading, failed refresh and empty states", () => {
  assert.match(
    render({ unreadUpdates: 2 }),
    /aria-label="Recurring tasks, 2 unread updates"/,
  );
  assert.match(render({ tasks: [], tasksLoading: true }), /Loading tasks/);
  const failed = render({ tasksError: "Could not refresh tasks." });
  assert.match(failed, /Retry tasks/);
  assert.match(failed, /Status unavailable/);
  assert.doesNotMatch(failed, /class="task-activity"/);
  assert.match(render({ tasks: [] }), /Create a recurring task/);
});
