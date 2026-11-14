from app.store import ChatStore
from app.runs import RunStore


def test_pins_persist_and_remain_owner_scoped(tmp_path):
    path = str(tmp_path / "chats.sqlite3")
    store = ChatStore(path)
    first = store.create("owner")
    second = store.create("owner")
    assert store.pin("other", first["id"], True) is None
    assert store.pin("owner", first["id"], True)["pinned"]
    reopened = ChatStore(path)
    assert reopened.list("owner")[0]["id"] == first["id"]
    assert reopened.detail("owner", first["id"])["pinned"]
    assert reopened.list("other") == []
    assert reopened.pin("owner", first["id"], False)["pinned"] == 0
    assert reopened.list("owner")[0]["id"] == second["id"]


def test_pinned_chats_are_still_searchable(tmp_path):
    store = ChatStore(str(tmp_path / "search.sqlite3"))
    chat = store.create("owner")
    store.append_exchange("owner", chat["id"], "Project notes", "Saved")
    store.pin("owner", chat["id"], True)
    assert store.list("owner", "project")[0]["pinned"]
    assert store.list("owner", "missing") == []


def test_run_message_ids_match_optimistic_messages(tmp_path):
    path = str(tmp_path / "runs.sqlite3")
    store, runs = ChatStore(path), RunStore(path)
    chat = store.create("owner")
    run = runs.create("owner", chat["id"], "request-id", "Hello", "auto")
    runs.claim()
    runs.checkpoint(run["id"], "answer_ready", {}, {"reply": "Hello back", "sources": []})
    runs.finish(run["id"], "completed", "finished")
    messages = store.detail("owner", chat["id"])["messages"]
    assert messages[0]["id"] == "user:request-id"
    assert messages[1]["id"] == f"assistant:{run['id']}"
