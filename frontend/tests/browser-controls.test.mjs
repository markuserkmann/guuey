import assert from "node:assert/strict";
import { test } from "node:test";
import { loadSource } from "./load-source.mjs";

const ui = await loadSource(
  [
    "./src/components/BrowserRequestCard.tsx",
    "./src/components/BrowserActivity.tsx",
    "./src/components/SavedLogins.tsx",
  ],
  true,
);
const base = {
  id: "request",
  run_id: "run",
  site: "example.com",
  status: "pending",
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 60_000).toISOString(),
  has_screenshot: false,
};
const renderRequest = (request, liveView = null) =>
  ui.renderToStaticMarkup(
    ui.createElement(ui.BrowserRequestCard, {
      token: "a".repeat(40),
      request,
      liveView,
      onRun() {},
      async onCancel() {},
    }),
  );

test("private input controls are masked and never rendered with saved values", () => {
  const fields = ["password", "otp", "magic_link"].map((kind) => ({
    id: kind,
    label: kind,
    kind,
    choices: [],
  }));
  const markup = renderRequest({
    ...base,
    kind: "input",
    payload: { reason: "Sign in", fields },
  });
  assert.equal((markup.match(/type="password"/g) || []).length, 3);
  assert.equal((markup.match(/autoComplete="off"/g) || []).length, 4);
  assert.ok(!markup.includes('value="'));
  assert.ok(!markup.includes("a".repeat(40)));
  assert.match(markup, /Other answers are shared with the assistant/);
  for (const kind of ["password", "otp", "magic_link"])
    assert.match(markup, new RegExp(`name="${kind}"`));
});

test("approval renders the exact summary and named target as escaped text", () => {
  const markup = renderRequest({
    ...base,
    kind: "approval",
    payload: {
      site: "example.com",
      url: "https://example.com/checkout",
      summary: "Order total €16.90 <script>alert(1)</script>",
      button_label: "Place order",
    },
  });
  assert.match(markup, /Order total €16.90 &lt;script&gt;/);
  assert.match(markup, /Place order/);
  assert.match(markup, /Approve action/);
  assert.match(markup, /Decline/);
  assert.ok(!markup.includes("<script>"));
});

test("expired requests remove approval and private input controls", () => {
  for (const kind of ["approval", "input", "takeover"]) {
    const markup = renderRequest({
      ...base,
      kind,
      expires_at: "2000-01-01T00:00:00Z",
      payload: { reason: "Login", fields: [] },
    });
    assert.match(markup, /request has expired/);
    assert.ok(!markup.includes("<button"));
    assert.ok(!markup.includes("<input"));
  }
});

test("takeover exposes the provider view with no referrer and keeps manual completion explicit", () => {
  const markup = renderRequest(
    { ...base, kind: "takeover", payload: { reason: "Finish verification" } },
    "https://hb.oxylabs.io/novnc/?id=private-session",
  );
  assert.match(markup, /rel="noopener noreferrer"/);
  assert.match(markup, /referrerPolicy="no-referrer"/);
  assert.match(markup, /I’m done/);
});

test("activity keeps uncertain submissions visible with an explicit review action", () => {
  const markup = ui.renderToStaticMarkup(
    ui.createElement(ui.BrowserActivity, {
      onReview() {},
      state: {
        steps: [],
        live_view_url: null,
        request: null,
        actions: [{ id: "action", summary: "Order", status: "unverified" }],
      },
    }),
  );
  assert.match(markup, /Result uncertain/);
  assert.match(markup, /Review uncertain actions/);
  assert.ok(!markup.includes("Try again"));
});

test("saved logins uses a named native dialog and hides import when unavailable", () => {
  const markup = ui.renderToStaticMarkup(
    ui.createElement(ui.SavedLogins, {
      token: "a".repeat(40),
      enabled: false,
      onClose() {},
      onChanged() {},
    }),
  );
  assert.match(markup, /<dialog[^>]+aria-labelledby="saved-logins-title"/);
  assert.match(markup, /Close saved logins/);
  assert.ok(!markup.includes('name="cookies"'));
  assert.match(markup, /cookie-helper.zip/);
});
