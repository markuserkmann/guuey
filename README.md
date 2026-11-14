# Guuey

Python backend using FastAPI and Pydantic, with a React and TypeScript frontend built by Vite.

## Docker

With Docker Engine and Docker Compose installed:

```sh
docker compose up --build
```

Open http://localhost:8080. API docs are at http://localhost:8000/docs, and the health endpoint is http://localhost:8000/api/health. The frontend proxies `/api` to the backend.

Stop containers with `docker compose down`.

If the default ports are occupied, override them:

```sh
BACKEND_PORT=18000 FRONTEND_PORT=18080 docker compose up --build
```

With these overrides, open http://localhost:18080 and API docs at http://localhost:18000/docs.

## Local development

Requires Python 3.12+, uv, and Node.js 22 with npm.

Backend:

```sh
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload
```

Frontend, in another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the local backend on port 8000.

## Checks

```sh
cd backend
uv run pytest
```

```sh
cd frontend
npm run build
```

Dependency resolutions are recorded in `backend/uv.lock` and `frontend/package-lock.json`. Use `uv lock` or `npm install` when intentionally updating dependencies.

Always obtain user permission before creating a Git commit.
