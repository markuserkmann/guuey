from datetime import UTC, datetime
from types import SimpleNamespace

from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, KnowledgeAnswer, KnowledgePoint, RequirementCheck, answer_coverage, render_answer
from app.orchestration.refinement import finish_research_result, merge_answers, prepared_action


URL = "https://hotel.example/product/rooms"


def source(text="Hotel A costs €140 per night.", url=URL, title="Hotel A", **kwargs):
    return {"url": url, "title": title, "text": text, "retrieval": "live",
            "retrieved_at": datetime.now(UTC).isoformat(), **kwargs}


def check(requirement_id="price", statement="€140 per night", quote="Hotel A costs €140 per night.",
          url=URL, status="met", **kwargs):
    return RequirementCheck(requirement_id=requirement_id, requirement=requirement_id, status=status, statement=statement,
                            evidence=[Evidence(source_url=url, quote=quote)] if quote else [], **kwargs)


def answer(checks=None, name="Hotel A", url=URL, kind="service"):
    return GroundedAnswer(findings=[Finding(name=name, source_url=url, source_kind=kind, checks=checks or [check()])])


def contract(**kwargs):
    return ResearchContract(activity="service", requirements=[Requirement(id="price", kind="price", description="Nightly price")], **kwargs)


def test_new_unknown_keeps_previously_supported_price():
    previous = answer()
    current = answer([check(status="unknown", statement="", quote="")])
    merged, trace = merge_answers(previous, current, [source()], contract())
    assert merged.findings[0].checks[0].status == "met"
    assert merged.findings[0].checks[0].statement == "€140 per night"
    assert trace["carried_checks"] == 1
    assert current.findings[0].checks[0].status == "unknown"


def test_absent_previous_candidate_is_preserved_when_source_remains_valid():
    previous = answer()
    current = answer(name="Hotel B", url="https://b.example", checks=[check(status="unknown", quote="")])
    sources = [source(), source("Hotel B room information", "https://b.example", "Hotel B")]
    merged, _ = merge_answers(previous, current, sources, contract())
    assert {finding.name for finding in merged.findings} == {"Hotel A", "Hotel B"}


def test_product_offers_do_not_merge_across_sellers_or_model_names():
    sources = [source("Laptop A costs €140 per night.", title="Laptop A"),
               source("Laptop A costs €140 per night.", "https://other.example/a", "Laptop A"),
               source("Laptop B details", "https://other.example/b", "Laptop B")]
    previous = answer(name="Laptop A", kind="product", checks=[check(quote=sources[0]["text"])])
    current = GroundedAnswer(findings=[
        answer(name="Laptop A", url=sources[1]["url"], kind="product", checks=[check(status="unknown", quote="")]).findings[0],
        answer(name="Laptop B", url=sources[2]["url"], kind="product", checks=[check(status="unknown", quote="")]).findings[0],
    ])
    request = ResearchContract(shopping=True, requirements=[Requirement(id="price", kind="price", description="Price")])
    merged, _ = merge_answers(previous, current, sources, request)
    assert len(merged.findings) == 3
    original = next(f for f in merged.findings if f.source_url == URL)
    assert next(c for c in original.checks if c.requirement_id == "price").status == "met"
    assert all(next(c for c in f.checks if c.requirement_id == "price").status == "unknown"
               for f in merged.findings if f.source_url != URL)


def test_conflicting_prices_become_unknown_and_cite_both_sources():
    second_url = "https://hotel.example/new-rates"
    sources = [source(), source("Hotel A costs €180 per night.", second_url)]
    current = answer([check(statement="€180 per night", quote=sources[1]["text"], url=second_url)], url=second_url)
    merged, trace = merge_answers(answer(), current, sources, contract())
    price = merged.findings[0].checks[0]
    assert price.status == "unknown" and len(price.evidence) == 2
    assert trace["conflicts"] == 1
    text, _ = render_answer(merged, sources)
    assert URL in text and second_url in text and "Conflicting:" in text
    repeated, _ = merge_answers(merged, current, sources, contract())
    assert repeated.findings[0].checks[0].status == "unknown"
    assert len(repeated.findings[0].checks[0].evidence) == 2
    stale = [{**item, "retrieved_at": "2020-01-01T00:00:00+00:00"} for item in sources]
    fresh = source("Hotel A costs €145 per night.", "https://hotel.example/today")
    latest = answer([check(statement="€145 per night", quote=fresh["text"], url=fresh["url"])], url=fresh["url"])
    refreshed, _ = merge_answers(merged, latest, [*stale, fresh], contract())
    assert refreshed.findings[0].checks[0].status == "met"


def test_dropped_quotes_are_not_restored_by_refinement():
    current = answer([check(status="unknown", statement="", quote="")])
    merged, trace = merge_answers(answer(), current, [source("Hotel A has no published room rate.")], contract())
    assert merged.findings[0].checks[0].status == "unknown"
    assert trace["carried_checks"] == 0


def test_stale_price_cannot_be_carried_even_if_quote_still_matches():
    current = answer([check(status="unknown", statement="", quote="")])
    merged, trace = merge_answers(answer(), current, [source(retrieved_at="2020-01-01T00:00:00+00:00")], contract())
    assert merged.findings[0].checks[0].status == "unknown"
    assert trace["carried_checks"] == 0


def test_preferences_do_not_create_hard_failure_after_merge():
    request = ResearchContract(activity="service", requirements=[
        Requirement(id="price", kind="price", description="Price"),
        Requirement(id="balcony", description="Prefer balcony", strength="preference")])
    sources = [source("Hotel A costs €140 per night. No balcony.")]
    current = answer([check(status="unknown", quote=""),
                      check("balcony", "No balcony", "No balcony", status="not_met")])
    merged, _ = merge_answers(answer(), current, sources, request)
    assert answer_coverage(merged, sources)["verified"] == 1


def related_contract():
    return ResearchContract(requirements=[
        Requirement(id="price", kind="price", description="Named hotel price", scope="related"),
        Requirement(id="availability", kind="availability", description="Same hotel availability", scope="related")])


def test_same_named_related_hotel_keeps_verified_price():
    text = "Hotel A is in Madeira. Hotel A costs €140 per night. Hotel A has rooms available."
    sources = [source(text)]
    previous = answer(name="Madeira", kind="other", checks=[check(quote=text, related_entity="Hotel A")])
    current = answer(name="Madeira", kind="other", checks=[
        check(status="unknown", quote="", related_entity="Hotel A"),
        check("availability", "Hotel A has rooms available", text, related_entity="Hotel A")])
    # Related facts must explicitly name the associated hotel.
    previous.findings[0].checks[0].statement = "Hotel A costs €140 per night"
    merged, _ = merge_answers(previous, current, sources, related_contract())
    assert all(c.status == "met" for c in merged.findings[0].checks)


def test_old_hotel_price_is_not_combined_with_different_hotel_availability():
    text_a = "Hotel A is in Madeira. Hotel A costs €140 per night."
    text_b = "Hotel B is in Madeira. Hotel B has rooms available."
    second_url = "https://hotelb.example/"
    sources = [source(text_a), source(text_b, second_url, "Hotel B")]
    previous = answer(name="Madeira", kind="other", checks=[
        check(statement="Hotel A costs €140 per night", quote=text_a, related_entity="Hotel A")])
    current = answer(name="Madeira", kind="other", url=second_url, checks=[
        check(status="unknown", quote="", related_entity="Hotel B"),
        check("availability", "Hotel B has rooms available", text_b, url=second_url, related_entity="Hotel B")])
    merged, _ = merge_answers(previous, current, sources, related_contract())
    assert next(c for c in merged.findings[0].checks if c.requirement_id == "price").status == "unknown"
    assert answer_coverage(merged, sources)["verified"] == 0


def test_multiple_current_related_hotels_cannot_form_one_verified_option():
    text = "Hotel A is in Madeira. Hotel A costs €140 per night. Hotel B is in Madeira. Hotel B has rooms available."
    sources = [source(text)]
    current = answer(name="Madeira", kind="other", checks=[
        check(statement="Hotel A costs €140 per night", quote=text, related_entity="Hotel A"),
        check("availability", "Hotel B has rooms available", text, related_entity="Hotel B")])
    merged, _ = merge_answers(GroundedAnswer(), current, sources, related_contract())
    assert all(c.status == "unknown" for c in merged.findings[0].checks)
    assert all("Conflicting related entities" in c.reason for c in merged.findings[0].checks)
    result = SimpleNamespace(reply="", sources=sources, debug={"output": current.model_dump_json()})
    result = finish_research_result(result, related_contract())
    assert result.debug["coverage"]["verified"] == 0
    assert result.debug["has_gaps"]


def test_prepared_message_is_only_a_draft_and_revalidates_evidence():
    request = contract(artifact="message_draft", missing_information=["Arrival date"])
    output = answer([check(quote="Fabricated hotel rate")])
    action = prepared_action(request, output, [source()])
    assert action["kind"] == "message_draft" and action["external_action"] is False
    assert "prepared only" in action["text"] and "Arrival date" in action["text"]
    assert "€140" not in action["text"]
    assert action["gaps"]


def test_finish_research_result_preserves_structured_verified_previous_work():
    current = answer([check(status="unknown", quote="")])
    result = SimpleNamespace(reply="Unverified", sources=[source()],
                             debug={"output": current.model_dump_json(), "coverage": {}})
    result = finish_research_result(result, contract(), answer().model_dump())
    assert result.debug["coverage"]["verified"] == 1
    assert not result.debug["has_gaps"]
    assert result.debug["refinement"]["carried_checks"] == 1
    assert "€140" in result.reply


def test_empty_planner_has_bounded_nonrepeating_queries_for_unknown_facts():
    from app.orchestration.contracts import ResearchContract
    from app.orchestration.interpretation import query_key
    from app.orchestration.refinement import fallback_gap_queries
    answer = {'findings': [{'name': name, 'checks': [
        {'requirement': 'Named budget hotel located here', 'status': 'unknown', 'statement': '', 'evidence': []},
        {'requirement': 'Under €150', 'status': 'not_met', 'statement': '', 'evidence': []},
    ]} for name in ['Tallinn', 'Tartu', 'Pärnu', 'Rakvere', 'Lahemaa']]}
    contract = ResearchContract(original_request='Hotels under €150 in Estonia', destination='Estonia')
    queries = fallback_gap_queries(answer, contract, set())
    assert len(queries) == 4
    assert all('under €150' in q.objective and 'Estonia' in q.query for q in queries)
    attempted = {query_key(q) for q in queries}
    remaining = fallback_gap_queries(answer, contract, attempted)
    assert len(remaining) == 1 and 'Lahemaa' in remaining[0].query
    attempted.update(query_key(q) for q in remaining)
    assert not fallback_gap_queries(answer, contract, attempted)


def test_knowledge_refinement_keeps_cited_points_and_counts_answer_as_complete():
    url = "https://kela.fi/example"
    first, second = "Support depends on household income.", "Apply through the official service."
    page = {"url": url, "title": "Kela support", "text": first + "\n\n" + second}
    point = lambda statement: KnowledgePoint(statement=statement, evidence=[Evidence(source_url=url, quote=statement)])
    previous = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point(first)]),
                             alignment_rejections=[{"value": "bad claim"}])
    current = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point(second)]),
                            alignment_rejections=[{"value": "bad claim"}])
    task = ResearchContract(activity="research", original_request="How do we apply for support?")
    result = finish_research_result(SimpleNamespace(reply="", sources=[page], debug={"output": current.model_dump_json()}),
                                    task, previous.model_dump())
    saved = GroundedAnswer.model_validate_json(result.debug["output"])
    assert {p.statement for p in saved.knowledge.points} == {first, second}
    assert saved.alignment_rejections == [{"value": "bad claim"}]
    assert result.debug["coverage"]["knowledge_points"] == 2
    assert not result.debug["has_gaps"]
    assert result.debug["next_action"]["kind"] == "none"
    assert first.rstrip(".") in result.reply and second.rstrip(".") in result.reply and "shortlist" not in result.reply
