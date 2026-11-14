# Guuey

A standalone React chat UI backed by FastAPI, Pydantic, and Pydantic AI. Chats are stored in SQLite, searchable by title and message, and scoped to the configured bearer token. Chat uses Gemini when configured and a deterministic local reply otherwise.

## Configuration

Copy `.env.example` to `.env`, set a unique `AGENT_API_TOKEN` with at least 32 characters, set `GEMINI_API_KEY`, and set `EXA_API_KEY` to enable web search. Both provider keys stay on the server. `GOOGLE_API_KEY` is also accepted for Gemini. `GEMINI_MODEL` selects the model and defaults to `gemini-3.5-flash-lite`.

Local development connects automatically through the frontend proxy. Production asks for the backend token and validates it before opening chats; the token stays in tab memory and clears on reload or disconnect. Chat history persists in a Docker volume; search filters saved titles and messages. Auto routes each request to chat or research. Auto and Fast allow two research rounds; Deep allows up to three rounds. Runs and debug traces persist in SQLite.

Debug opens beside the conversation on desktop and below it on mobile, so chat remains usable. The composer accepts text, Markdown, CSV, JSON, and code files through the attachment button or drag-and-drop. Attach up to three UTF-8 files per message, 50 KB each and 100 KB combined. File contents are sent with the message to the model and saved with the chat for follow-up questions. Attached files can be expanded in the conversation. Images, PDFs, and office documents are not supported yet.

## Run with Docker

```sh
docker compose up --build
```

Open http://localhost:18000. The backend API and docs are at http://localhost:18001. The Debug panel reports provider, model, backend health, and whether API key auth is configured.

The example environment sets `COMPOSE_FILE=compose.yaml:compose.dev.yaml`, so this command starts development with automatic authentication and live reload.

Set `BACKEND_PORT` and `FRONTEND_PORT` to override the host ports.

## Docker development with live reload

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Open http://localhost:18000. React and CSS edits update through Vite hot reload. Backend Python edits restart Uvicorn automatically; worker Python edits restart its supervisor. Source mounts are read-only inside the containers, and the existing chat data volume is retained.

Development connects automatically using the configured `AGENT_API_TOKEN`. The Vite proxy adds the token on the server; it is never bundled into browser code or saved in browser storage. This is enabled only in the development Compose override. The normal production containers still require the backend token. For local Vite outside Docker, set `DEV_AUTH_TOKEN` to the same value as the backend token in the server environment.

The chat uses assistant-ui thread and message primitives. Sent messages appear immediately with delivery feedback; failed submissions can be retried with the same request ID. Run progress and checkpointed answers update while the worker runs. Pinned chats are saved in the backend and remain searchable.

An edit during a research run can interrupt it; the saved run and any checkpointed result remain available. Let a run finish before editing when you want to preserve its execution.

Rebuild with the same command when dependencies or Dockerfiles change. Source edits do not require image rebuilds. To return to the normal containers:

```sh
docker compose -f compose.yaml up -d --build
```

## Local development

Start the API:

```sh
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload
```

Run `uv run python -m app.worker` from `backend/` in a second terminal, using the same environment and database path as the API.

Start the UI in another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000.
