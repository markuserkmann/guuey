from datetime import UTC, datetime

import pytest

from app.orchestration.contracts import Requirement, ResearchContract
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.refinement import merge_answers


URL = "https://retailer.example/product/1051696"
SHORT = 'HP OmniBook 3 16-by0016no 16" laptop'
LONG = SHORT + ", silver, Windows 11 Home"


def finding(name, field="price", url=URL):
    statement = f"{name} costs €599." if field == "price" else f"{name} has 16 GB RAM."
    return Finding(name=name, source_url=url, source_kind="product", checks=[
        RequirementCheck(requirement_id=field, requirement=field, status="met", statement=statement,
                         evidence=[Evidence(source_url=url, quote=statement)])])


def request():
    return ResearchContract(shopping=True, original_request="Compare offers from retailer.example", requirements=[
        Requirement(id="price", kind="price", description="Published price"),
        Requirement(id="memory", description="16 GB RAM"),
    ])


def sources(*findings):
    urls = dict.fromkeys(f.source_url for f in findings)
    return [{"url": url, "title": "Laptop offers", "retrieval": "live", "retrieved_at": datetime.now(UTC).isoformat(),
             "text": "\n\n".join(check.statement for f in findings if f.source_url == url for check in f.checks)} for url in urls]


def test_same_exact_offer_and_model_merge_descriptive_name_variations():
    old, new = finding(SHORT), finding(LONG, "memory")
    merged, trace = merge_answers(GroundedAnswer(findings=[old]), GroundedAnswer(findings=[new]), sources(old, new), request())
    assert len(merged.findings) == 1
    assert merged.findings[0].name == SHORT
    checks = {check.requirement_id: check for check in merged.findings[0].checks}
    assert checks["price"].status == checks["memory"].status == "met"
    assert trace["carried_checks"] == 1


def test_current_round_duplicates_also_merge_complementary_evidence():
    first, second = finding(SHORT), finding(LONG, "memory")
    merged, _ = merge_answers(GroundedAnswer(), GroundedAnswer(findings=[first, second]), sources(first, second), request())
    assert len(merged.findings) == 1
    assert {check.requirement_id for check in merged.findings[0].checks if check.status == "met"} == {"price", "memory"}


@pytest.mark.parametrize("left,right", [
    (SHORT, SHORT.replace("by0016no", "by0017no")),
    (SHORT + " 16GB RAM", SHORT + " 32GB RAM"),
    (SHORT + " 512GB SSD", SHORT + " 1TB SSD"),
    (SHORT + " Intel i5", SHORT + " Intel i7"),
    (SHORT, SHORT.replace('16"', '14"')),
    ("Laptop A", "Laptop B"),
])
def test_same_url_does_not_merge_distinct_explicit_models_or_configurations(left, right):
    old, new = finding(left), finding(right, "memory")
    merged, _ = merge_answers(GroundedAnswer(findings=[old]), GroundedAnswer(findings=[new]), sources(old, new), request())
    assert {item.name for item in merged.findings} == {left, right}
    newer = next(item for item in merged.findings if item.name == right)
    assert next(check for check in newer.checks if check.requirement_id == "price").status == "unknown"


def test_same_model_at_different_sellers_remains_separate():
    old, new = finding(SHORT), finding(LONG, "memory", "https://other.example/product/1051696")
    merged, _ = merge_answers(GroundedAnswer(findings=[old]), GroundedAnswer(findings=[new]), sources(old, new), request())
    assert len(merged.findings) == 2


def test_ambiguous_unconfigured_name_does_not_bridge_two_variants():
    old = finding(SHORT)
    first, second = finding(SHORT + " 16GB RAM", "memory"), finding(SHORT + " 32GB RAM", "memory")
    merged, _ = merge_answers(GroundedAnswer(findings=[old]), GroundedAnswer(findings=[first, second]), sources(old, first, second), request())
    assert len(merged.findings) == 3
    assert all(next(check for check in item.checks if check.requirement_id == "price").status == "unknown"
               for item in merged.findings if item.name != SHORT)


def test_same_current_price_does_not_conflict_with_old_price_label():
    old, new = finding(SHORT), finding(SHORT)
    old.checks[0].statement = f'{SHORT}: Price now €599, regular price €799.'
    old.checks[0].evidence[0].quote = old.checks[0].statement
    merged, trace = merge_answers(GroundedAnswer(findings=[old]), GroundedAnswer(findings=[new]), sources(old, new), request())
    assert next(c for c in merged.findings[0].checks if c.requirement_id == 'price').status == 'met'
    assert trace['conflicts'] == 0
