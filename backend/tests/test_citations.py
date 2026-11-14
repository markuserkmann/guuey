from app.orchestration.grounding import GroundedAnswer, render_answer


SOURCES = [{"url": "https://shop.example/product", "text": "Laptop A has 16 GB RAM. Price €900.", "title": "Shop"}]


def answer(quote="Laptop A has 16 GB RAM.", url="https://shop.example/product"):
    return GroundedAnswer.model_validate({"findings": [{"name": "Laptop A", "checks": [
        {"requirement": "16 GB RAM", "status": "met", "statement": "16 GB installed", "evidence": [{"source_url": url, "quote": quote}]},
        {"requirement": "Linux support", "status": "unknown", "statement": "", "evidence": []},
    ]}]})


def test_partial_finding_keeps_supported_requirement_and_link():
    text, issues = render_answer(answer(), SOURCES)
    assert "16 GB installed" in text
    assert "https://shop.example/product" in text
    assert "Unverified" in text
    assert not issues


def test_unknown_url_does_not_support_claim():
    text, issues = render_answer(answer(url="https://invented.example"), SOURCES)
    assert "16 GB installed" not in text
    assert "https://invented.example" not in text
    assert issues


def test_fabricated_quote_does_not_support_claim():
    text, issues = render_answer(answer(quote="Linux certified"), SOURCES)
    assert "16 GB installed" not in text
    assert issues


def test_bad_finding_does_not_discard_good_finding():
    result = answer()
    result.findings.extend(answer(quote="Invented evidence").findings)
    text, issues = render_answer(result, SOURCES)
    assert "16 GB installed" in text
    assert issues
