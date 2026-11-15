# Guuey

Guuey researches options, compares them against your requirements, and takes the next steps on websites with an agent you can watch and take over.

- Fully agentic workflows that research options and take the next steps.
- Recurring tasks that send matching updates directly to Telegram.
- A live agent browser that users can watch and take over.
- Live web research through Exa, with citations and freshness checks.
- User approval before final purchases, bookings, or form submissions.
- Food ordering assistance that finds delivery options and prepares a basket.
- Encrypted saved website logins for future tasks.
- Live stock and appointment checks through interactive websites.
- Personalized comparisons based on budget, dates, location, and preferences.
- Follow-up requests that continue with the existing browser session.
- Voice input for handing off tasks by speaking.
- Live web research and Markdown parsing with Exa: retrieved pages become structured, cited evidence while preserving headings, lists, and table context.

The chat also supports named user accounts, searchable and pinned conversations, text attachments, streamed progress, and a Debug panel with research and browser activity. Features depend on the configured providers; setup and operating limits are documented below.

## Example tasks

These are example requests, not recorded results:

| Task | Example request |
|---|---|
| Food delivery | Find vegetarian delivery options near my address under €25 and prepare a basket. Ask before placing the order. |
| Product research | Compare three laptops under €900 that ship to Finland. Check stock, keyboard layout, warranty, and delivery costs. |
| Appointment checks | Check available appointments for this provider next Tuesday after 16:00. Show me the slots before booking. |
| Travel | Compare hotels for my dates and budget, then open the selected hotel's booking page. |
| Recurring research | Check new vendor security advisories every hour and send matching updates to Telegram. |
| Follow-up | Keep the current basket, replace the drink, and show the updated total. |

## Run locally with Docker

You need Docker with Compose. Run these commands from the repository root:

```sh
cp .env.example .env
```

Edit `.env` with the credentials for the features you want:

| Feature | Configuration |
|---|---|
| Model replies and voice input | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Exa research and recurring checks | Gemini credentials plus `EXA_API_KEY` |
| Live remote browser | `BROWSER_ENABLED=true`, Gemini credentials, `OXY_AB_USERNAME`, and `OXY_AB_PASSWORD` |
| Browser website discovery | `OXYLAB_KEY` or `OXYLABS_WEB_API_KEY`; otherwise supply a website URL |
| Saved logins and private sign-in inputs | A stable `BROWSER_SESSION_KEY` containing 64 hexadecimal characters |
| Telegram delivery | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and the `telegram` Compose profile |

Start the API, worker, and frontend, then create a user:

```sh
docker compose up -d --build
docker compose exec backend /app/.venv/bin/python -m app.accounts create alice --password --base-url http://localhost:18000
```

The account command prompts for a password and prints a reusable magic link. Open [Guuey](http://localhost:18000) and sign in. The backend exposes [health](http://localhost:18001/api/health) and [interactive API documentation](http://localhost:18001/docs).

The example environment enables the development Compose override with live reload. For the production container layout, use `docker compose -f compose.yaml up -d --build`; see [deployment and operations](docs/operations.md) for HTTPS, persistence, and backups. Without Gemini credentials, ordinary chat uses local mock replies.

## How tasks run

The worker routes each request to chat, Exa research, browser interaction, or a clarification. Store, price and pickup lookups use web search first. Research extracts the task's requirements, retrieves sources, checks cited claims, and refines missing evidence within the selected Fast, Auto, or Deep limits. When browser verification could help, the app keeps the search results and asks before starting the slower browser check. Explicit browser requests and website actions can start directly.

Browser tasks can navigate, compare options, fill reversible fields, and prepare baskets. The agent requests approval for final actions and checks the resulting page for confirmation. Uncertain submissions require the user to inspect and resolve the outcome before another submission on that site. Final-action detection uses heuristics; see [browser controls and limitations](docs/browser.md).

After a browser reply, a follow-up in the same chat can reuse the page and basket for 60 seconds by default, configurable up to 300 seconds. Saved logins persist separately. Recurring tasks continue while the app is closed when the worker is running; Telegram delivery requires a linked account and opt-in on each task.

## Documentation

The separate [docs/](docs/README.md) folder contains the full documentation:

| Guide | Covers |
|---|---|
| [Getting started](docs/getting-started.md) | Docker, local development, credentials, first account, and first task |
| [Configuration](docs/configuration.md) | Environment variables, defaults, limits, and service configuration |
| [Using Guuey](docs/user-guide.md) | Chat, task examples, attachments, progress, and follow-ups |
| [Exa research](docs/research.md) | Markdown parsing, citations, freshness, caching, and refinement |
| [Live browser](docs/browser.md) | Watching, takeover, approvals, food baskets, saved logins, and browser settings |
| [Recurring tasks](docs/recurring-tasks.md) | Scheduling, conditions, deduplication, history, and notifications |
| [Telegram](docs/telegram.md) | Bot setup, linking, opt-in, delivery, and disconnecting |
| [Voice input](docs/voice-input.md) | Dictation, microphone permissions, token flow, and language settings |
| [Accounts](docs/accounts.md) | Provisioning, passwords, magic links, sessions, and seeded chats |
| [API reference](docs/api.md) | Authentication, endpoints, request examples, and event streams |
| [Architecture](docs/architecture.md) | Services, data flow, persistence, modules, and trust boundaries |
| [Development and testing](docs/development.md) | Tests, builds, browser fixtures, and live evaluations |
| [Operations](docs/operations.md) | Deployment, logs, backups, upgrades, and troubleshooting |
| [Aiven Runtime](docs/aiven-runtime.md) | Container deployment, managed PostgreSQL, TLS, secrets, and redeployment |

## Repository layout

```text
backend/
  app/                 FastAPI, workers, research, browser, accounts, and storage
  app/orchestration/   Routing, task interpretation, planning, grounding, verification
  app/browser/         Browser tools, sessions, approvals, and live-view state
  tests/               Backend regression and browser fixture tests
  evaluations/         Quality scoring and versioned gold cases
  examples/            Seed conversation example
frontend/
  src/                 React chat UI and API clients
  cookie-helper/       Optional browser extension for exporting login cookies
  tests/               Frontend contract and behavior tests
docs/                  Setup, user, API, and maintenance guides
compose.yaml           Base container services
compose.dev.yaml       Development reload and source mounts
compose.eval.yaml      Bounded research evaluation overrides
Dockerfile.aiven        Combined frontend, API, and worker image
compose.aiven.yaml      Runtime deployment template
deploy/                Process supervision for the combined image
.env.example           Configuration template without credentials
```

The backend uses Python, FastAPI, Pydantic AI, Gemini, Exa, and Playwright or Patchright. The frontend uses React, TypeScript, Vite, assistant-ui, and Markdown rendering. The default Compose deployment stores application data in SQLite in a shared volume.
