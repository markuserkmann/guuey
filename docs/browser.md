# Live browser workflows and saved logins

[Documentation index](README.md)

## Food baskets, stock, and appointments

Give the agent the website or delivery service, location, budget, quantities, and
preferences. It can find delivery options, select products, make reversible
choices, and prepare a basket. It checks the basket before claiming an item was
added. Review the contents, substitutions, fees, and total before approving the
final order. Website access and available controls determine what can be completed.

For stock and appointment checks, include the exact variant, service, provider,
date, or time window. Availability applies to the configuration actually checked.
The agent discloses provisional choices and asks for private information or a
commitment when needed. Looking for an appointment does not authorize booking it.

## Enable and route browser tasks

Set `BROWSER_ENABLED=true` to make the Gemini browser agent available. The composer's
**Force browser** checkbox is a debugging override and starts unchecked.
The run API accepts `force_browser` (default `false`); retries and Deep continuations
retain that run's choice. Unchecked leaves automatic routing and browser handoffs
enabled. Checked bypasses model routing and starts the browser directly. An explicit
browser choice in the message, including a trailing `browser`, also bypasses routing.
Explicit requests not to use a browser still take precedence over the debug override.
Live staff availability and booking-calendar checks are browser tasks.
Requests to order, buy, book, submit forms, send messages, or change website accounts
are browser tasks even when finding or comparing options comes first. Clear action
requests bypass model classification and go directly to the browser. If browser
support is disabled, these requests report that it is unavailable rather than running
as research. Information requests and unsent drafts still use normal routing.

When workflow confidence is below 0.8, a local rules check resolves the choice without
another model or network call. Clear form, login, basket and booking interactions or
live appointment checks select the browser; other uncertain requests start with agentic
search. Drafts, explanations and negated interactions also stay on search. This check
uses the current message, not instructions in attachments or earlier turns. Low
confidence about research depth alone does not trigger workflow reclassification.
The debug override and explicit browser opt-outs still take precedence.

If research leaves requested live availability or stock unverified, the same run can
continue in the browser with its discovered site links. A failed search provider can
also hand off to the browser with the checkbox unchecked. Browser opt-outs, disabled browser support and draft-only
results prevent the availability handoff. A staff profile alone does not complete an
appointment-availability check. Across sites, the agent advances visible forms using
explicit requirements, known preferences, then plausible reversible defaults. It records
provisional service, duration or other choices and discloses them with the result;
availability applies only to the checked configuration. Follow-ups retain these choices
until the user changes them. After two unproductive routes, or when a choice requires
private facts or a commitment, it asks for the missing information. Checking slots does
not authorize making a booking.

Unverified provider links and incomplete suitability checks trigger targeted follow-up
research within the selected mode's round and time limits. If those searches stall or
run out of useful queries, the worker checks whether to continue in the browser before
returning a partial answer. Interactive pages, JavaScript-only content and requested
stock hidden at checkout can hand off immediately. Browser work carries the unresolved
requirements and supported facts forward; it requires enabled server support and no
explicit browser opt-out. If browser verification is disabled or unavailable, the partial
answer states that limitation. Debug records the recovery decision and its reason.

## Connection and same-chat follow-ups

The default backend is Oxylabs: one remote browser connection stays open for the run,
including while waiting for private input or approval. Playwright connects over CDP
with a URL-safe Basic authorization header; browser credentials stay out of the URL.
Connections use `page_handler=false` and optional country targeting. Missing credentials
or a remote connection failure do not start a local browser.

After a successful browser reply, the chat worker keeps that page and connection
open for 60 seconds (`BROWSER_IDLE_TIMEOUT_S`, configurable from 1 to 300 seconds).
The reply is saved immediately, so the user can send another message during this
window. A browser follow-up in the same chat reuses the page, including its basket
and form state, with fresh task limits and approval requests. Each follow-up still
goes through the normal router; a non-browser task ends the warm session after it
finishes. Idle browsers do not occupy active task slots. Expiry, cancellation,
connection loss, or a changed/deleted saved login closes the connection. Idle page
network requests are blocked until a new browser task starts.

When information or manual interaction is needed during a task, the agent uses
the existing input or takeover card and keeps the live view available while
waiting. This uses `BROWSER_REQUEST_TIMEOUT_S`, not the 60-second idle window.
The live-view link is exposed only during an active task. A normal `needs_user`
reply also retains the page briefly; errors and uncertain submissions close it.

## Page loading

Images, media and fonts are blocked before download by default. The browser agent
can use `open(url, load_images=true)` when images are needed for visual review or
manual interaction. That choice applies before navigation and persists through
clicks and form submissions until the next `open` call. Text-only work and fresh
login-verification sessions keep images blocked. Stylesheets and JavaScript remain
enabled so visibility checks, dynamic forms and the VNC layout continue to work.
The VNC preview reflects this setting: text-only tasks may show missing images.
Enabling images requires a subsequent navigation; the app does not automatically
reload forms, resubmit actions or reuse one-time sign-in links to restore images.

## Approval and uncertain submissions

The agent can navigate, fill forms, restore and save encrypted logins, and request
approval before final actions. It checks that the page and target still match the
approval, records each submission, and requires new page text to confirm the result.
An uncertain submission blocks further submissions on that site until the owner
checks the account and resolves it through the API. Passwords, one-time codes and
sign-in links use encrypted private input requests and are removed from stored responses
when consumed. Sign-in links are restricted to the login site and opened at most once.
Card fields require manual takeover; saved payment methods can be used.

Run responses and SSE events include browser steps, pending requests, action status,
and the owner-only live-view link. Debug includes browser model calls and timing data.
Cancellation and expiry clear stored private responses, and browser runs cannot
continue as Deep research. The final-action label and URL checks are heuristics;
approval remains necessary for final submissions.

## Watch and take over

Browser activity appears in the conversation with expandable steps and a read-only
VNC iframe preview for active Oxylabs sessions. The preview scales to fit the chat;
Open live view opens the interactive viewer in a new tab, also available if the
embed cannot connect. Local Chromium sessions do not provide a VNC preview.
Approval cards show the proposed action, website button, screenshot and expiry.
Private-input forms mask passwords, one-time codes and sign-in links, and clear fields
when sent or expired. Takeover cards let you open the live view and confirm when the
manual step is finished. Browser runs do not offer automatic retries or Deep research.

## Manage saved logins and action outcomes

Open **Saved logins** in the header to import or delete a login. Deletion asks for
confirmation because it also stops tasks using that login. The same panel lists
uncertain submissions: check the website account, select the outcome and record what
you found before starting another task on that site. The controls use the authenticated
API; a request's `kind` determines its response body:

- `POST /api/runs/{id}/browser/requests/{request_id}` accepts `{"decision":"approve"}`
  or `{"decision":"decline"}`, `{"values":{"field_id":"value"}}`, or `{"done":true}`.
- `GET /api/runs/{id}/browser/requests/{request_id}/screenshot` returns the approval image.
- `GET /api/browser/actions` lists uncertain submissions for the current owner.
- `POST /api/browser/actions/{id}/resolve` accepts
  `{"decision":"confirmed","evidence":"Order 123 appears in my account"}` or
  `{"decision":"not_completed","evidence":"No order in my account history"}`.
  Only uncertain actions from finished runs can be resolved.

Offline API and fixture tests do not establish that a particular live website or
the remote viewer currently works. When changing these flows, verify the viewer,
request controls, and target-site behavior manually; see [testing](development.md).

## Cookie helper

The saved-logins panel includes **Download cookie helper**. Unzip the download, open
`chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select
the extracted folder. On the website tab, open the helper and export cookies, then
paste them into Guuey. The helper requests access only to that HTTPS site, removes
the permission after the operation, and makes no network requests. Cookie values
remain on the clipboard until you replace them. The helper does not export local
storage; some sites still require signing in through the browser task.

`npm run dev` and `npm run build` generate `frontend/public/cookie-helper.zip` from
`frontend/cookie-helper/`. Validate the frontend with
`cd frontend && npm test && npm run build && npm run format`.

## Encryption and login scope

Set `BROWSER_SESSION_KEY` to a persistent 32-byte key encoded as 64 hex characters:

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

Saved state uses AES-GCM encryption. The API and worker must use the same key. Without a valid key, importing saved logins
and collecting passwords, one-time codes or sign-in links return unavailable. Changing the key makes
existing encrypted sessions unreadable; delete them and import them again. Sessions
belong to the signed-in user. People using the same legacy API token share its saved logins.

Authenticated session endpoints:

- `GET /api/browser/sessions` lists site names, status, revisions and cookie counts.
- `POST /api/browser/sessions/import` accepts `{"site_url":"https://example.com","cookies":"session=..."}`.
- `DELETE /api/browser/sessions/{id}` removes a saved login and revokes its active lease.

Saved logins have exclusive leases tied to the owner, site and run. A run may use
one authenticated HTTPS site, including its subdomains. The worker renews the lease
while waiting for the model or user; deletion, cancellation and expiry stop further
browser actions and close the connection. Imports return 409 while a login is leased.
Revision checks reject stale saves, including saves after deletion and replacement.
Cancelled and interrupted runs release leases and preserve uncertain submissions.

Saving a login checks account evidence in separate fresh anonymous and authenticated
browser connections. The evidence must be absent anonymously and present with the
stored state, without a visible password or OTP form. Verification uses an account
URL without query parameters or fragments. Inconclusive checks leave the state
`unverified`. Cookies and local storage are restricted to the site; expired cookies
are discarded, and refreshed state is saved under the run's lease. Cross-site SSO
redirects are not supported within an authenticated run; import the destination
site's login cookies instead.

Import accepts helper JSON, cookie-editor JSON, Netscape cookie files, or a Cookie
header. Imports start as `unverified`; values are never returned by the API. Imports
are limited to 500 cookies and 1 MiB, and reject expired, partitioned, unrelated-domain
and public-suffix cookies. An import with no valid cookies leaves the saved login intact.

## Provider and driver configuration

Browser settings are listed in [`.env.example`](../.env.example) and the [configuration reference](configuration.md) and passed to both services by Compose.
`BROWSER_ENABLED` defaults to false, `BROWSER_BACKEND` to `oxylabs`, and
`BROWSER_MODEL` to `gemini-3.5-flash-lite`. Oxylabs configuration uses `OXY_AB_USERNAME`,
`OXY_AB_PASSWORD`, and optional `BROWSER_COUNTRY`. Website discovery uses the separate
`OXYLAB_KEY` (also accepted as `OXYLABS_WEB_API_KEY`) with the Oxylabs MCP `search` tool.
Other remote MCP tools are unavailable. Without the discovery key, the agent can open
supplied website URLs and asks for a URL when needed. Browser discovery does not fall
back to the research search provider when Oxylabs is selected.

`BROWSER_DRIVER=patchright` selects the patched Playwright-compatible driver for either
backend. `BROWSER_BACKEND=oxylabs` still connects to the same remote provider and uses its
default context and live view; `BROWSER_BACKEND=local` launches a local browser. The default
driver remains `playwright`; switching drivers requires a worker restart. Run diagnostics
include the selected driver. Patchright changes automation behavior but does not guarantee
CAPTCHA-free access or change the remote browser's launch flags, exit IP or provider settings.
Oxylabs challenge handling is currently disabled (`page_handler=false`) independently of
the driver. Use the live view to inspect a blocked page; provider recordings can also be
enabled separately for diagnosis. Patchright does not deliver browser console events.

## Execution limits and recovery

Local development settings include
`BROWSER_HEADLESS`; deadline, tool-call and user-response limits use
`BROWSER_DEADLINE_S`, `BROWSER_MAX_STEPS`, and `BROWSER_REQUEST_TIMEOUT_S`.
Browser runs default to 30 minutes, 120 tool calls and 15 minutes for user responses.
`BROWSER_THINKING_LEVEL=minimal` keeps routine browser decisions fast. Page controls
come directly from Playwright's accessibility snapshot without a separate model call.
`BROWSER_MODEL_MAX_TOKENS=8192` and `BROWSER_MODEL_TIMEOUT_S=180` retain room for longer responses.
`BROWSER_TOOL_RETRIES=5` allows corrections to invalid tool arguments; it never
authorizes repeating a final submission. Validation diagnostics record the tool,
field paths and error types without input values. Model-request counts remain bounded,
and the run's hard deadline remains 59 minutes including user waits.
Oxylabs connections use six timed attempts with exponential backoff and jitter for
transient connection errors. Authentication, invalid parameters, profile conflicts
and restricted targets stop without retrying the same connection request. Browser
cleanup still runs on every exit; an established task is never replayed automatically.

## Website interaction details

Recognized cookie dialogs are closed automatically, or accepted when no usable close
or necessary-only control remains, including English, Finnish and Swedish controls. Detection is scoped
to cookie containers and dialogs; unrelated acceptance buttons are left alone. Each
consent control is attempted once per frame URL. Pending final actions disable this
handling until their result is checked. Repeated actions on unchanged pages and
repeated navigation cycles are blocked after two attempts; continued repetition ends
the run with a request for help instead of consuming the entire browser budget.
Page observations prioritize foreground dialogs and address suggestions over long
catalogs, including product popups rendered in portal regions without a dialog role.
Popup inspection preserves page references. Control checks read DOM attributes without
replacing the accessibility reference map, including while the page is updating.
Repeated picker open/select cycles count
as repetition even when each click succeeds. Restored-login status is included in the
agent context; current account controls must still establish whether it is signed in.
Browser tools accept both plain (`e45`) and frame-prefixed (`f21e45`) element references,
including popup detection and repeat tracking. Authenticated interactions in frames
remain restricted to the saved login's site.
Text inspection supports `ref` to read a whole table or date group and `refresh=false`
to search or paginate the last observation without another browser round trip. Inspection
refreshes by default; the agent must verify live availability with a fresh observation.
Text waits check the main page and embedded frames, including frames attached while waiting,
under one shared timeout. Navigation observations allow up to two seconds for empty or
spinner-only embedded forms to show readable content before handing the page to the model.
Product cards omit duplicated descendant text only when their accessible names already
contain those details; prices, sellers and availability stay together. Query excerpts include
the containing result and parent references. Pagination preserves its query and subtree.
Recent inspection evidence survives history trimming with obsolete references removed.
The first inspection after an action refreshes pending results before cached pagination resumes.
Repeated identical reads receive guidance, then stop instead of consuming the whole step budget.
Native selects and custom listbox pickers support exact-label selection. Quantity changes count
as progress. Search fields without forms support Enter while ordinary inputs and checkout
submissions retain their guards. Ordinary checkout buttons outside forms are not submissions.
Once browsing a site, direct navigation uses user-provided or observed URLs; invented paths
and sorting parameters are rejected. Links opening a new tab become the active page, and Back
can return to the original results with their filters intact. Warm follow-ups retain that tab.
Foreground controls remain visible during text searches, and covered background
targets are refused before clicking. Product previews lead through their merchant's
ordering controls; the agent checks basket contents before claiming an item was added.
An address field may be an editable combobox: the agent selects a matching
suggestion before continuing. Overlay errors retain their actual obstruction details
in debug history. Repeated overlay failures or exhausted progress offer one manual
takeover when live view is available, then stop if recovery remains unsuccessful.
The evaluation Compose configuration keeps browser automation disabled.

## Local browser fixtures

`BROWSER_BACKEND=local` is available for development and fixture tests. Install its
Chromium version with `cd backend && uv run playwright install chromium`, or
`cd backend && uv run patchright install chromium` for Patchright. The remote backend
does not require browser binaries in the Docker image. Local flow tests cover both
drivers and skip a driver when its Chromium is missing or the environment denies startup.
For offline backend validation without launching browsers, run
`cd backend && uv run --frozen pytest -q --ignore=tests/test_browser_flows.py -k 'not real_chromium'`.
