from app.orchestration import orchestrator
from app.research import SearchUnavailable


def test_parallel_searches_deduplicate_and_fetch_pages(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_searches", lambda message: (["one", "two"], None))
    def search(query):
        return [{"url": "https://shared.example/", "title": "Shared", "text": query},
                {"url": f"https://{query}.example/", "title": query, "text": query}]
    monkeypatch.setattr(orchestrator, "search", search)
    seen = []
    def extract(urls, *, limit=3):
        seen.extend(urls)
        return [{"url": urls[0], "title": "Page", "text": "full page"}]
    monkeypatch.setattr(orchestrator, "extract_pages", extract)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda message, history, sources: ("answer", {}))
    result = orchestrator.run_turn("find items", [], search_requested=True)
    assert len(seen) == 3
    assert result.sources[0]["text"] == "full page"
    assert result.sources[1]["text"] in {"one", "two"}


def test_batch_continues_when_a_search_fails(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_searches", lambda message: (["bad", "good"], None))
    def search(query):
        if query == "bad":
            raise SearchUnavailable("failed")
        return [{"url": "https://good.example/", "title": "Good", "text": "evidence"}]
    monkeypatch.setattr(orchestrator, "search", search)
    monkeypatch.setattr(orchestrator, "extract_pages", lambda *args, **kwargs: (_ for _ in ()).throw(SearchUnavailable("failed")))
    monkeypatch.setattr(orchestrator, "generate_reply", lambda message, history, sources: ("answer", {}))
    assert orchestrator.run_turn("query", [], search_requested=True).sources[0]["text"] == "evidence"
