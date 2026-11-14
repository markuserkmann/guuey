import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main, worker
from app.attachments import TextAttachment
from app.orchestration.decisions import RouteDecision
from app.runs import RunStore
from app.store import ChatStore

FILES = [{"name": "requirements.md", "content": "Budget: €900. Memory: 16 GB."}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_DATABASE_PATH", str(tmp_path / "chats.sqlite3"))
    monkeypatch.setenv("AGENT_API_TOKEN", "a" * 40)
    main.get_store.cache_clear()
    main.get_runs.cache_clear()
    with TestClient(main.app, headers={"Authorization": "Bearer " + "a" * 40}) as client:
        yield client
    main.get_store.cache_clear()
    main.get_runs.cache_clear()


def test_attached_file_reaches_worker_and_survives_reload_and_continuation(client, monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "decide", lambda *args: RouteDecision(workflow="chat", confidence=1))

    def generate(message, history, **kwargs):
        seen.append(message)
        return SimpleNamespace(reply="Budget is €900.", sources=[], debug={"has_gaps": True})

    monkeypatch.setattr(worker, "run_turn", generate)
    chat_id = client.post("/api/chats").json()["id"]
    body = {"message": "Read the requirements", "request_id": str(uuid4()), "attachments": FILES}
    run = client.post(f"/api/chats/{chat_id}/runs", json=body)
    assert run.status_code == 202
    run_id = run.json()["id"]
    assert client.post(f"/api/chats/{chat_id}/runs", json=body).json()["id"] == run_id
    changed = {**body, "attachments": [{**FILES[0], "content": "Changed"}]}
    assert client.post(f"/api/chats/{chat_id}/runs", json=changed).status_code == 409
    RunStore().claim()
    worker.execute(run_id)
    assert FILES[0]["content"] in seen[0]
    saved = client.get(f"/api/chats/{chat_id}").json()["messages"]
    assert saved[0]["content"] == body["message"]
    assert saved[0]["attachments"] == FILES
    assert saved[1]["attachments"] == []
    child = client.post(f"/api/runs/{run_id}/continue")
    assert child.status_code == 202
    assert child.json()["attachments"] == FILES
    assert child.json()["parent_id"] == run_id


@pytest.mark.parametrize("files", [
    [{"name": "file.pdf", "content": "%PDF-1.5"}],
    [{"name": "../secret.txt", "content": "no"}],
    [{"name": "file.txt", "content": "binary\u0000"}],
    [{"name": "file.txt", "content": " "}],
    [{"name": "file.txt", "content": "€" * 18000}],
    [{"name": f"file{i}.txt", "content": "x" * 40000} for i in range(3)],
    [{"name": f"file{i}.txt", "content": "x"} for i in range(4)],
    FILES * 2,
])
def test_invalid_attachments_are_rejected_by_api(client, files):
    chat_id = client.post("/api/chats").json()["id"]
    response = client.post(f"/api/chats/{chat_id}/runs", json={
        "message": "Read", "request_id": str(uuid4()), "attachments": files,
    })
    assert response.status_code == 422
    assert client.get(f"/api/chats/{chat_id}/runs/latest").json() is None


def test_attachment_data_is_owner_scoped(client, monkeypatch):
    chat_id = client.post("/api/chats").json()["id"]
    run = client.post(f"/api/chats/{chat_id}/runs", json={
        "message": "Read", "request_id": str(uuid4()), "attachments": FILES,
    }).json()
    monkeypatch.setenv("AGENT_API_TOKEN", "b" * 40)
    headers = {"Authorization": "Bearer " + "b" * 40}
    assert client.get(f"/api/runs/{run['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/chats/{chat_id}", headers=headers).status_code == 404


def test_existing_message_table_migrates_without_losing_content(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT, sources TEXT, created_at TEXT)")
        db.execute("INSERT INTO messages VALUES('m', 'c', 'user', 'Existing message', '[]', 'today')")
    ChatStore(path)
    ChatStore(path)
    with sqlite3.connect(path) as db:
        content, files = db.execute("SELECT content, attachments FROM messages").fetchone()
    assert content == "Existing message"
    assert json.loads(files) == []


def test_followup_model_input_contains_saved_file(monkeypatch):
    from app import chat
    from app.schemas import ChatTurn
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, TextPart

    prompts = []

    async def model(messages, info):
        prompts.extend(part.content for message in messages for part in message.parts if part.part_kind == "user-prompt")
        return ModelResponse(parts=[TextPart("€900")])

    monkeypatch.setattr(chat, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(chat, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    history = [ChatTurn(role="user", content="Read this", attachments=[TextAttachment(**FILES[0])])]
    reply, _ = chat.generate_reply("What was the budget?", history)
    assert reply == "€900"
    assert FILES[0]["content"] in prompts[0]
