# Recurring tasks

[Documentation index](README.md)

The authenticated task API stores recurring research in the same application database as
chats. The existing worker checks for due tasks every five seconds, queues them under
its normal concurrency limit, and runs fresh web research followed by a structured
evaluation of the notification condition. Both Gemini and Exa must be configured on
the worker. In-app updates use the existing worker; optional Telegram delivery runs in
a separate process.

## Create a task

Create an hourly CVE watch with `POST /api/tasks`:

```json
{
  "title": "Critical CVE watch",
  "prompt": "Check newly published CVEs using CVE.org, NVD, CISA, and vendor security advisories. Include CVE IDs, affected versions, severity and score source, confirmed exploitation, patches or mitigations, and primary-source links.",
  "notification_condition": "Notify me only about a newly published CVE rated Critical by its vendor or with a verified CVSS v3.x/v4.0 score of at least 9.0. Do not repeat previously reported events.",
  "interval_minutes": 60,
  "timezone": "Europe/Tallinn",
  "enabled": true,
  "telegram_enabled": false
}
```

`GET /api/task-templates` supplies a reusable CVE example; it does not activate a task.
The critical threshold follows [FIRST's CVSS severity scale](https://www.first.org/cvss/v4.0/specification-document#Qualitative-Severity-Rating-Scale).
Open **Recurring tasks** in the left sidebar, then **New task** or **Use hourly CVE example**
to fill the form. Review its instructions and alert condition, then select **Create task**.
The Tasks panel supports editing, pause/resume, Check now, history, and deletion.
The **Updates** view lists matching results with sources; the sidebar heading shows the unread
count. Open chat reads the full conversation, and
updates can be marked read individually or together. The panel refreshes every 10 seconds.
No recurring tasks are activated just by installing this feature or selecting a template.

Recurring task chats have their own section in the left sidebar, separate from Pinned
and Recents. Select a task to read its chat; select the Recurring tasks heading to manage
schedules and read updates. Queued and running checks show a rotating arrow, including
when another chat is open. After each refresh, the sidebar waits 3 seconds before polling
again if any check is queued or running, or 10 seconds otherwise. It also refreshes when
the window regains focus. Reduced-motion preferences keep the arrow still. An open task
chat picks up newly scheduled runs automatically.

## Task API and scheduling

| Endpoint | Behavior |
|---|---|
| `GET /api/tasks` | List the current owner's tasks and latest status |
| `POST /api/tasks` | Create a task and its alert conversation |
| `PUT /api/tasks/{id}` | Replace the title, instructions, condition and schedule |
| `PATCH /api/tasks/{id}` | Pause or resume with `{"enabled": false}` or `true` |
| `POST /api/tasks/{id}/run` | Queue one immediate check, including for a paused task |
| `GET /api/tasks/{id}/runs` | Read the most recent 50 checks and their outcomes |
| `DELETE /api/tasks/{id}` | Delete the task, history and inbox entries; retain its chat |
| `GET /api/task-notifications` | Read the latest 100 updates and total unread count |
| `POST /api/task-notifications/{id}/read` | Mark one update as read |
| `POST /api/task-notifications/read-all` | Mark all of the current owner's updates as read |

## Interval semantics

Intervals are elapsed minutes, from 1 to 10,080 (one week), with up to 50 tasks per
owner. The first automatic run is one interval after creation. Resume and interval
changes restart that countdown; Check now also resets the next scheduled time.
The timezone supplies research context; these are intervals, not local clock-time
or cron schedules. Pausing prevents future scheduling and lets an already queued
or running check finish. Editing and deletion are rejected while the conversation has an active run; retry after it becomes idle.

Schedules and alert deduplication survive restarts. Downtime is coalesced into one
catch-up check, and a task never overlaps an active run in its conversation. A worker
interruption or provider failure is recorded as a failed check, not as a successful
check with no matches. Resume processing requires the API and worker to use the same
database. Checks continue while the browser is closed as long as the worker is running.

## Freshness and alert evaluation

Recurring checks bypass the local page and seller-policy caches and request a live crawl
for fetched pages (`maxAgeHours: 0`). Cached provider fallbacks and discovery-only text
are excluded from the evidence used to evaluate alerts. If no live pages can be retrieved,
the check fails rather than reporting a successful check from cached evidence. Existing
retrieval limits still apply; live access and complete coverage are not guaranteed.
Previous results and notified-event keys are retained for duplicate suppression.
Ordinary interactive research keeps its existing cache behavior.

Only supported, matching, previously unreported events create inbox entries and
assistant messages in the task's chat. Each alert's evidence quotes are checked against
retrieved source text, and stable event keys suppress duplicate notifications. Quiet
checks appear only in run history. Research uses a 24-hour overlap before the previous
successful check (or the preceding interval on the first run) to catch indexing delays.
This is model-evaluated web monitoring, not a complete CVE feed: search coverage,
indexing delays, source access and model classification can cause missed events or
incorrect judgments. Review the cited advisories. Delivery is persisted in the app;
email, browser push and operating-system notifications are not implemented.

## Deliver matches to Telegram

Link and confirm your private Telegram account, then enable **Send matches to
Telegram** on each desired task. Linking alone does not send results, and existing
inbox entries are not backfilled. The regular worker creates research results;
the optional Telegram worker delivers queued updates. See [Telegram setup and
behavior](telegram.md).
