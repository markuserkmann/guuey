from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.grounding import GroundedAnswer, answer_coverage, apply_contract, render_answer
from app.research import _sources, diverse_sources, evidence_windows
from datetime import UTC, datetime


URL = "https://seller.fi/laptop-a"
CONTRACT = ResearchContract(shopping=True, destination="Finland", country_code="FI", requested_count=10,
                            requirements=[Requirement(id="price", kind="price", description="Under €1,000")])


def offer(kind="product"):
    return GroundedAnswer.model_validate({"requested_count": 5, "findings": [{
        "name": "Laptop A", "source_url": URL, "source_kind": kind,
        "assessment": "Definitely available in Finland with Linux support.",
        "checks": [{"requirement_id": key, "requirement": key, "status": "met", "statement": text,
                    "evidence": [{"source_url": URL, "quote": text}]} for key, text in [
                        ("price", "Laptop A costs €900."), ("stock", "In stock."), ("delivery", "Delivery to Finland.")]],
    }]})


def sources(retrieval="live"):
    return [{"url": URL, "title": "Laptop A", "text": "Laptop A costs €900. In stock. Delivery to Finland.",
             "retrieval": retrieval, "retrieved_at": datetime.now(UTC).isoformat()}]


def test_omitted_finland_and_stock_checks_cannot_complete():
    answer = offer()
    answer.findings[0].checks = answer.findings[0].checks[:1]
    output = apply_contract(answer, sources(), CONTRACT)
    assert output.requested_count == 10
    assert answer_coverage(output, sources())["verified"] == 0
    assert [c.status for c in output.findings[0].checks] == ["met", "unknown", "unknown"]
    text, _ = render_answer(output, sources())
    assert "Finland: **Unverified**" in text
    assert "Definitely available" not in text


def test_review_prices_and_search_snippets_cannot_verify_offers():
    for kind, retrieval in [("review", "live"), ("comparison", "live"), ("product", "discovery")]:
        output = apply_contract(offer(kind), sources(retrieval), CONTRACT)
        assert answer_coverage(output, sources(retrieval))["verified"] == 0
        if kind == "product":
            assert output.findings[0].checks[0].status == "unknown"
        else:
            assert not output.findings


def test_live_product_with_explicit_destination_is_verified():
    output = apply_contract(offer(), sources(), CONTRACT)
    assert answer_coverage(output, sources())["verified"] == 1
    assert "multiple retailers" in output.limitations[0]


def test_shipping_policy_must_belong_to_the_seller():
    answer = offer()
    other = {"url": "https://other.fi/shipping", "title": "Shipping", "text": "Delivery to Finland.", "retrieval": "page"}
    answer.findings[0].checks[-1].evidence[0].source_url = other["url"]
    output = apply_contract(answer, sources() + [other], CONTRACT)
    assert output.findings[0].checks[-1].status == "unknown"
    other["url"] = "https://seller.fi/shipping"
    answer.findings[0].checks[-1].evidence[0].source_url = other["url"]
    output = apply_contract(answer, sources() + [other], CONTRACT)
    assert output.findings[0].checks[-1].status == "met"


def test_failed_live_fetch_is_not_accepted_as_fresh_evidence():
    payload = {"results": sources(), "statuses": [{"id": URL, "status": "error", "error": {"tag": "CRAWL_TIMEOUT"}}]}
    assert not _sources(payload, text_limit=12000, retrieval="live")


def test_old_run_cannot_supply_current_prices_or_stock():
    old = sources()
    old[0]["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    output = apply_contract(offer(), old, CONTRACT)
    assert [c.status for c in output.findings[0].checks][:2] == ["unknown", "unknown"]


def test_country_availability_is_one_mandatory_delivery_check():
    contract = ResearchContract(shopping=True, destination="Finland", requirements=[
        Requirement(id="location", description="Laptop available in Finland", kind="fact"),
        Requirement(id="made_in", description="Manufactured in Finland", kind="fact"),
    ])
    assert [r.id for r in contract.requirements] == ["made_in", "stock", "delivery"]


def test_source_budget_includes_other_retailers_before_duplicate_domains():
    candidates = [{"url": f"https://review.example/{i}"} for i in range(20)]
    candidates += [{"url": "https://retailer.fi/product"}, {"url": "https://second.fi/product"}]
    assert diverse_sources(candidates, 3) == [candidates[0], candidates[-2], candidates[-1]]


def test_evidence_after_long_navigation_is_kept_with_product_context():
    text = "Menu navigation links. " * 1000 + "\nLaptop A 16GB €900. In stock. Delivery to Finland.\n" + "Footer " * 2000
    excerpt = evidence_windows(text, 12000, "16GB laptop Finland under €1000")
    assert len(excerpt) <= 12000
    assert "Laptop A 16GB €900. In stock. Delivery to Finland." in excerpt


def test_search_sends_objective_and_country_and_contents_requests_live(monkeypatch):
    from app import research
    calls = []
    def request(endpoint, body):
        calls.append((endpoint, body))
        return {"results": sources()}
    monkeypatch.setattr(research, "_request", request)
    research.search("16GB kannettava", objective="Find offers at Finnish retailers", country="FI")
    research.extract_pages([URL], fresh=True)
    assert calls[0][1]["userLocation"] == "FI"
    assert calls[0][1]["objective"] == "Find offers at Finnish retailers"
    assert calls[1][1]["maxAgeHours"] == 0
