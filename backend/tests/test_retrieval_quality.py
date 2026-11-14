import pytest

from app import research
from app.orchestration.passages import find_passage


@pytest.mark.parametrize("limit", [0, 1, 100, 1800, 3600, 4000, 12000])
def test_evidence_windows_respect_budget(limit):
    text = "Navigation links. " * 5000 + "Sauna Helsinki private booking €120."
    assert len(research.evidence_windows(text, limit, "Sauna Helsinki booking")) <= limit


def test_evidence_beyond_60k_is_retrievable():
    fact = "Private Helsinki sauna accommodates 12 people for €120 per hour."
    text = "Menu navigation links. " * 4000 + "\n\n" + fact + "\n\n" + "Footer. " * 1000
    assert fact in research.evidence_windows(text, 4000, "Private Helsinki sauna 12 people price")


def test_small_budget_prioritizes_relevant_evidence_over_navigation():
    fact = "Madeira hiking holiday coastal paths."
    text = "Navigation links. " * 300 + fact + " Footer. " * 300
    assert fact in research.evidence_windows(text, 500, "Madeira hiking holiday")


def test_adjacent_windows_do_not_repeat_price_evidence():
    fact = "Laptop A 16GB costs €900 and ships to Finland."
    text = "Navigation. " * 130 + fact + " More navigation. " * 1500
    excerpt = research.evidence_windows(text, 4000, "Laptop A 16GB Finland")
    assert excerpt.count(fact) == 1


def test_cached_fallback_is_not_live_evidence():
    payload = {"results": [{"url": "https://example.fi/sauna", "text": "Private sauna €120."}],
               "statuses": [{"id": "https://example.fi/sauna", "status": "success", "source": "cached"}]}
    source = research._sources(payload, text_limit=4000, retrieval="live")[0]
    assert source["retrieval"] == "page"
    assert not research.live_source(source)


def test_failed_document_id_cannot_become_fresh_redirected_url():
    payload = {"results": [{"id": "original", "url": "https://example.fi/new", "text": "Old price €120."}],
               "statuses": [{"id": "original", "status": "error"}]}
    assert research._sources(payload, text_limit=4000, retrieval="live") == []


def test_search_window_uses_query_for_relevance(monkeypatch):
    fact = "Madeira hiking coastal paths open year round."
    text = "Shop €99. " * 3000 + fact + " Footer. " * 3000
    monkeypatch.setattr(research, "_request", lambda *args: {
        "results": [{"url": "https://example.org", "text": text}]})
    assert fact in research.search("Madeira hiking coastal paths")[0]["text"]


@pytest.mark.parametrize("fresh", [False, True])
def test_extraction_keeps_full_page_and_passage_context(monkeypatch, fresh):
    from app.orchestration.passages import citation_passages

    text = "# Laptop A\n\n" + "Navigation and general information.\n\n" * 2000
    text += "## Current offer\n\n| Configuration | Price |\n|---|---|\n| 16 GB / 512 GB | €679.90 |\n\n"
    text += "## Delivery\n\nShips to Finland via Posti for €3.90."
    calls = []
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append((endpoint, body)) or {
        "results": [{"url": "https://example.fi/laptop", "text": text}]})
    source = research.extract_pages(["https://example.fi/laptop"], fresh=fresh)[0]
    assert source["text"] == text
    refs = citation_passages(source)
    offer = next(context for quote, context in refs.values() if "679.90" in quote)
    assert "Laptop A" in offer and "Current offer" in offer and "Configuration | Price" in offer
    assert any("ships to finland" in quote for quote, _ in refs.values())
    assert calls[0][1]["text"] == {"verbosity": "full", "linkStyle": "full"}
    assert "maxCharacters" not in str(calls[0][1])


def test_search_retains_full_evidence_and_urls_without_snippets(monkeypatch):
    text = "# Sauna A\n\n" + "General visitor information.\n\n" * 2000 + "Adult public session: €28 for two hours."
    monkeypatch.setattr(research, "_request", lambda *args: {"results": [
        {"url": "https://example.fi/sauna", "text": text},
        {"url": "https://example.fi/booking", "title": "Booking calendar"}]})
    sources = research.search("Sauna A current price")
    assert sources[0]["text"] == text
    assert sources[1]["url"] == "https://example.fi/booking"
    assert sources[1]["text"] == ""


def test_targeted_subpages_remain_independent_sources_with_own_freshness(monkeypatch):
    calls = []
    parent = "https://seller.fi/laptop"
    policy = "https://seller.fi/delivery"
    stock = "https://seller.fi/availability"
    payload = {"results": [{"url": parent, "text": "Laptop A €679.", "subpages": [
        {"url": policy, "text": "We ship to Finland."},
        {"url": stock, "text": "Laptop A: 5 in stock."}]}], "statuses": [
        {"id": parent, "status": "success", "source": "crawled"},
        {"id": stock, "status": "success", "source": "crawled"}]}
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append(body) or payload)
    sources = research.extract_pages([parent], fresh=True, subpage_targets=["delivery", "shipping"])
    assert calls[0]["subpages"] == 2
    assert calls[0]["subpageTarget"] == ["delivery", "shipping"]
    assert [source["url"] for source in sources] == [parent, policy, stock]
    assert [source["retrieval"] for source in sources] == ["live", "page", "live"]
    assert sources[1]["text"] == "We ship to Finland."
    assert not research.live_source(sources[1])


def test_failed_or_duplicate_subpages_do_not_replace_primary_evidence():
    parent = "https://seller.fi/laptop"
    payload = {"results": [{"url": parent, "text": "Current offer.", "subpages": [
        {"url": parent, "text": "Duplicate child text."},
        {"url": "https://seller.fi/failed", "text": "Old policy."}]}], "statuses": [
        {"id": "https://seller.fi/failed", "status": "error"}]}
    assert [source["text"] for source in research._sources(payload)] == ["Current offer."]


@pytest.mark.parametrize("targets", ["delivery", [""], ["x" * 101], [None]])
def test_invalid_subpage_targets_fail_before_network(monkeypatch, targets):
    monkeypatch.setattr(research, "_request", lambda *args: pytest.fail("Unexpected network request"))
    with pytest.raises(ValueError):
        research.extract_pages(["https://seller.fi/laptop"], subpage_targets=targets)


def test_offer_links_follow_named_product_and_ignore_neighbouring_models_and_footer():
    source = {"url": "https://seller.fi/category/laptops", "text": '''
[Asus 14" Vivobook Go 14 E1404FA, kannettava tietokone, Black](/products/e1404fa-black)
[Asus 14" Vivobook Go 14 E1404FA, kannettava tietokone, Cool Silver](/products/e1404fa-eb1529w)
[Asus 14" Vivobook Go 14 E1404FA, kannettava tietokone, Cool Silver](https://seller.fi/products/e1404fa-eb1529w)
[Asus Vivobook Go 15 E1504FA](/products/e1504fa)
[Lenovo V14 G4, kannettava tietokone, musta](/products/lenovo-v14-g4)
[Asus](/brand/asus)
[Lenovo](/brand/lenovo)
'''}
    assert research.offer_links(source, 'Asus 14" Vivobook Go 14 E1404FA, kannettava tietokone, Cool Silver') == [
        "https://seller.fi/products/e1404fa-eb1529w"]
    assert research.offer_links(source, "Lenovo V14 G4, kannettava tietokone, musta") == [
        "https://seller.fi/products/lenovo-v14-g4"]
    assert research.offer_links(source, "Lenovo V15 G4") == []


def test_offer_links_support_reference_links_and_exact_model_code_without_inventing_urls():
    source = {"url": "https://seller.fi/products", "text": '''
[See this offer][offer]
[Other laptop](/products/14arp100)
[Another product](/products/14arp10x)
[Download brochure](/manuals/14arp10.pdf)

[offer]: /products/14arp10
'''}
    assert research.offer_links(source, "Lenovo IdeaPad Slim 3 14ARP10") == ["https://seller.fi/products/14arp10"]
    assert research.offer_links(source, "Lenovo IdeaPad Slim 3 15ARP10") == []


def test_offer_links_do_not_read_model_numbers_from_image_paths():
    source = {"url": "https://seller.fi/category/laptops", "text": '''
[ ![Lenovo 14" IdeaPad Slim 5 14ARP10, Luna Grey](https://images.example/product/3/0/image.jpg "Lenovo 14" IdeaPad Slim 5 14ARP10, Luna Grey") ](/products/slim5)
[Lenovo 14" IdeaPad Slim 3 14ARP10, Luna Grey](/products/slim3)
'''}
    assert research.offer_links(source, 'Lenovo 14" IdeaPad Slim 3 14ARP10, Luna Grey') == [
        "https://seller.fi/products/slim3"]


def test_offer_links_reject_unsafe_urls_media_and_generic_cpu_matches():
    source = {"url": "https://seller.fi/list", "text": '''
[Lenovo V14 G4](https://user:password@seller.fi/product/1)
[Lenovo V14 G4](javascript:alert(1))
[Lenovo V14 G4](https://seller.fi/photos/product.png)
[Lenovo V14 G4](/search?q=LenovoV14G4)
[Lenovo V14 G4](/Product/List/1)
[Lenovo V14 G4](#product)
[Generic Ryzen 5 processor](/products/cpu/ryzen5)
'''}
    assert research.offer_links(source, "Lenovo V14 G4") == []
    assert research.offer_links(source, "Asus Ryzen5 laptop") == []


def test_skipped_heading_levels_do_not_inherit_sibling_package():
    text = "# Sauna A\n\n### Public session\n\n10 € per person.\n\n### Private hire\n\n120 € per hour."
    match = find_passage("120 € per hour", text)
    assert match is not None
    assert "Sauna A" in match.context
    assert "Private hire" in match.context
    assert "Public session" not in match.context


def test_extraction_retains_original_url_for_redirect_mapping():
    payload = {"results": [{"id": "https://example.fi/old", "url": "https://example.fi/new", "text": "Current prices."}]}
    source = research._sources(payload, text_limit=4000)[0]
    assert source["requested_url"] == "https://example.fi/old"
    assert source["url"] == "https://example.fi/new"


@pytest.mark.parametrize("include_original_url", [True, False])
def test_redirected_extraction_reaches_answer(monkeypatch, include_original_url):
    from app.orchestration import orchestrator
    from app.orchestration.contracts import ResearchContract, SearchPlan, SearchQuery

    plan = SearchPlan(contract=ResearchContract(), queries=[SearchQuery(query="sauna", objective="prices")])
    discovered = {"url": "https://example.fi/old", "title": "Sauna", "text": ""}
    extracted = {"url": "https://example.fi/new", "title": "Sauna", "text": "Current price €120", "retrieval": "live"}
    if include_original_url:
        extracted["requested_url"] = discovered["url"]
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (plan, None))
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: [discovered])
    monkeypatch.setattr(orchestrator, "extract_pages", lambda *args, **kwargs: [extracted])
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    result = orchestrator.run_turn("Sauna price", [], search_requested=True)
    assert extracted in result.sources
    if include_original_url:
        assert discovered not in result.sources
