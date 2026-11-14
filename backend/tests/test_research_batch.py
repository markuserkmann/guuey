from app.orchestration import orchestrator
from app.research import SearchUnavailable
from app.orchestration.contracts import ResearchContract, SearchPlan, SearchQuery


def plan(*queries):
    return SearchPlan(contract=ResearchContract(), queries=[SearchQuery(query=q, objective=q) for q in queries]), None


def test_parallel_searches_deduplicate_and_reuse_full_search_text(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: plan("one", "two"))
    def search(query, **kwargs):
        return [{"url": "https://shared.example/", "title": "Shared", "text": query},
                {"url": f"https://{query}.example/", "title": query, "text": query}]
    monkeypatch.setattr(orchestrator, "search", search)
    seen = []
    def extract(urls, **kwargs):
        seen.extend(urls)
        return [{"url": urls[0], "title": "Page", "text": "full page"}]
    monkeypatch.setattr(orchestrator, "extract_pages", extract)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    result = orchestrator.run_turn("find items", [], search_requested=True)
    assert len(seen) == 0
    assert result.sources[0]["text"] == "one"
    assert result.sources[1]["text"] in {"one", "two"}


def test_batch_continues_when_a_search_fails(monkeypatch):
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: plan("bad", "good"))
    def search(query, **kwargs):
        if query == "bad":
            raise SearchUnavailable("failed")
        return [{"url": "https://good.example/", "title": "Good", "text": "evidence"}]
    monkeypatch.setattr(orchestrator, "search", search)
    monkeypatch.setattr(orchestrator, "extract_pages", lambda *args, **kwargs: (_ for _ in ()).throw(SearchUnavailable("failed")))
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    assert orchestrator.run_turn("query", [], search_requested=True).sources[0]["text"] == "evidence"


def test_retains_candidates_beyond_ten_without_refetching_text(monkeypatch):
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: plan('retail', 'compatibility'))
    monkeypatch.setattr(orchestrator, 'search', lambda query, **kwargs: [
        {'url': f'https://example.com/{query}/{i}', 'title': query, 'text': 'snippet'}
        for i in range(10)
    ])
    def extract(urls, **kwargs):
        return [{'url': url, 'title': 'Product', 'text': 'Full specifications'} for url in urls]
    monkeypatch.setattr(orchestrator, 'extract_pages', extract)
    monkeypatch.setattr(orchestrator, 'generate_reply', lambda *args, **kwargs: ('answer', {}))
    result = orchestrator.run_turn('top ten laptops', [], search_requested=True)
    assert len(result.sources) == 20
    assert all(source['text'] == 'snippet' for source in result.sources)


def test_followup_sources_survive_seed_limit(monkeypatch):
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: plan('new'))
    fresh = {'url': 'https://new.example/', 'title': 'Certification', 'text': 'Linux support'}
    monkeypatch.setattr(orchestrator, 'search', lambda query, **kwargs: [fresh])
    monkeypatch.setattr(orchestrator, 'extract_pages', lambda *args, **kwargs: [fresh])
    monkeypatch.setattr(orchestrator, 'generate_reply', lambda *args, **kwargs: ('answer', {}))
    seeds = [{'url': f'https://old.example/{i}', 'title': 'Old', 'text': 'snippet'} for i in range(30)]
    result = orchestrator.run_turn('Linux support', [], search_requested=True, seed_sources=seeds)
    assert fresh in result.sources
    assert len(result.sources) == 30


def test_full_text_preferred_to_short_highlights():
    from app.research import _sources
    sources = _sources({'results': [{'url': 'https://example.com/product',
        'text': 'Laptop A. 16 GB RAM. Price €900.', 'highlights': ['Laptop A.']}]}, text_limit=12000)
    assert '16 GB RAM' in sources[0]['text']
    assert '€900' in sources[0]['text']
