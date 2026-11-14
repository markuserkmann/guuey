# Guuey

A standalone React chat UI backed by FastAPI, Pydantic, and Pydantic AI. Chats are stored in SQLite, searchable by title and message, and scoped to the configured bearer token. Chat uses Gemini when configured and a deterministic local reply otherwise.

## Configuration

Copy `.env.example` to `.env`, set a unique `AGENT_API_TOKEN` with at least 32 characters, set `GEMINI_API_KEY`, and set `EXA_API_KEY` to enable web search. Both provider keys stay on the server. `GOOGLE_API_KEY` is also accepted for Gemini. `GEMINI_MODEL` selects the model and defaults to `gemini-3.5-flash-lite`.

The browser asks for the backend token at connect time. It is held in tab memory only and is cleared on reload or disconnect. Chat history persists in a Docker volume; search filters saved titles and messages. Checking **Search with Exa** runs one bounded web search before Gemini writes a source-linked answer.

## Run with Docker

```sh
docker compose up --build
```

Open http://localhost:18000. The backend API and docs are at http://localhost:18001. The Debug panel reports provider, model, backend health, and whether API key auth is configured.

Set `BACKEND_PORT` and `FRONTEND_PORT` to override the host ports.

## Local development

Start the API:

```sh
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload
```

Start the UI in another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000.
