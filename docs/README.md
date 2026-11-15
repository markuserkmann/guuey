# Guuey documentation

[Project README](../README.md)

Guuey combines conversational research, website actions, recurring checks, and notifications. Start with the guide for the work you need to do.

## Install and operate

| Guide | Contents |
|---|---|
| [Getting started](getting-started.md) | Docker and local setup, provider requirements, first account, first task |
| [Configuration](configuration.md) | Environment variables, defaults, model selection, storage, and browser limits |
| [Accounts](accounts.md) | Account CLI, authentication, reusable magic links, and conversation imports |
| [Operations](operations.md) | Deployment, persistence, backups, logs, upgrades, and troubleshooting |
| [Aiven Runtime](aiven-runtime.md) | Container deployment, managed PostgreSQL, TLS, secrets, and redeployment |

## Use the application

| Guide | Contents |
|---|---|
| [Using Guuey](user-guide.md) | Task instructions, conversations, attachments, modes, and progress |
| [Exa research](research.md) | Retrieval, Markdown parsing, citations, freshness checks, and refinement |
| [Live browser](browser.md) | Live view, takeover, approvals, baskets, authenticated sessions, and follow-ups |
| [Recurring tasks](recurring-tasks.md) | Schedules, notification conditions, deduplication, and history |
| [Telegram](telegram.md) | Administrator setup, account linking, per-task opt-in, and delivery outcomes |
| [Voice input](voice-input.md) | Dictation controls, languages, and the browser-to-Gemini audio flow |

## Develop and integrate

| Guide | Contents |
|---|---|
| [API reference](api.md) | HTTP endpoints, authentication, examples, event streams, and errors |
| [Architecture](architecture.md) | Service responsibilities, request lifecycle, modules, storage, and data handling |
| [Development and testing](development.md) | Local checks, browser fixtures, live research evaluations, and quality scoring |

These guides describe the repository's implementation and configured defaults. Model names are configuration values in this project, not a guarantee of provider availability. Live website behavior depends on access, sign-in state, page structure, and provider responses. Historical evaluation reports are identified as such; they do not certify current results.
