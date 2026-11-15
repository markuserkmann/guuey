import assert from "node:assert/strict";
import { test } from "node:test";
import { loadSource } from "./load-source.mjs";

const api = await loadSource(["./src/taskApi.ts"]);
const token = "a".repeat(40);

test("task chat restores its scheduled check when ordinary chat history is empty", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (path) => {
    calls.push(path);
    if (path.endsWith("/latest")) return Response.json(null);
    if (path === "/api/tasks/task/runs")
      return Response.json([
        { run_id: "check", created_at: "2026-11-15T10:00:00Z" },
      ]);
    assert.equal(path, "/api/runs/check");
    return Response.json({ id: "check", status: "running" });
  });
  assert.deepEqual(await api.latestTaskRun(token, "chat", "task"), {
    id: "check",
    status: "running",
  });
  assert.equal(calls.length, 3);
});

test("a newer manual conversation run takes precedence over an old scheduled check", async (t) => {
  const manual = { id: "manual", created_at: "2026-11-15T11:00:00Z" };
  t.mock.method(globalThis, "fetch", async (path) => {
    if (path.endsWith("/latest")) return Response.json(manual);
    assert.equal(path, "/api/tasks/task/runs");
    return Response.json([
      { run_id: "check", created_at: "2026-11-15T10:00:00Z" },
    ]);
  });
  assert.deepEqual(await api.latestTaskRun(token, "chat", "task"), manual);
});

test("a newer scheduled check replaces the previous conversation run", async (t) => {
  t.mock.method(globalThis, "fetch", async (path) => {
    if (path.endsWith("/latest"))
      return Response.json({
        id: "manual",
        created_at: "2026-11-15T09:00:00Z",
      });
    if (path === "/api/tasks/task/runs")
      return Response.json([
        { run_id: "check", created_at: "2026-11-15T10:00:00Z" },
      ]);
    assert.equal(path, "/api/runs/check");
    return Response.json({ id: "check", status: "queued" });
  });
  assert.equal((await api.latestTaskRun(token, "chat", "task")).id, "check");
});
