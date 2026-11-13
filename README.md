# Guuey

A React chat interface with a small FastAPI/Pydantic backend. The current backend is a deterministic local mock for exercising chat and the Debug menu. It does not call a model provider.

## Run with Docker

```sh
docker compose up --build
```

Open http://localhost:18000. The Debug menu reports backend health and mock mode. The backend API is available at http://localhost:18001/docs.

If those ports are busy, override `BACKEND_PORT` and `FRONTEND_PORT` when starting Compose.

Set `GEMINI_API_KEY` in the environment or in a local `.env` file to enable Gemini. `GOOGLE_API_KEY` is also accepted. The default model is `gemini-3.5-flash-lite`; set `GEMINI_MODEL` to choose another Gemini model. Without a key the API stays in local mock mode.

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

Open http://localhost:5173. The Vite server proxies `/api` to port 8000.
