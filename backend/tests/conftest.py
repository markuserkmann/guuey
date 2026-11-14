import pytest


@pytest.fixture(autouse=True)
def isolate_source_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_CACHE_PATH", str(tmp_path / "research-cache.sqlite3"))
