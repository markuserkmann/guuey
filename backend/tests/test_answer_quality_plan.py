"""Regressions for the audited failures F1–F11 and their acceptance boundaries."""
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import Budget, Requirement, ResearchContract
from app.orchestration.grounding import (
    Evidence, Finding, GroundedAnswer, KnowledgeAnswer, KnowledgePoint, LLMAnswer,
    RequirementCheck, alignment_ok, apply_contract, expand_llm_answer,
    named_hotel_alternative, render_answer,
)
from app.orchestration.passages import citation_passages
from app.orchestration.refinement import fallback_gap_queries
from app.orchestration.verification import verify_claims
from app.source_quality import classify, source_class


PAGES = json.loads((Path(__file__).parent / 'fixtures/answer-quality-pages.json').read_text())


def finding(source, name, value, *, identifier='fact', kind='other'):
    return Finding(name=name, source_url=source['url'], source_kind=kind, checks=[
        RequirementCheck(requirement_id=identifier, requirement=identifier, status='met',
            value=value, statement=value, evidence=[Evidence(source_url=source['url'], quote=source['text'])])])


def test_f1_a_bad_knowledge_point_does_not_erase_cited_good_points():
    source = PAGES['loyly_public']
    sentence = source['text'].split('. ', 1)[0] + '.'
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[
        KnowledgePoint(statement=sentence, evidence=[Evidence(source_url=source['url'], quote=sentence)]),
        KnowledgePoint(statement='Admission is always free.', evidence=[Evidence(source_url=source['url'], quote=sentence)]),
    ]))

    async def review(messages, info):
        claims = json.loads(messages[0].parts[-1].content)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'judgments': [
            {'claim_id': c['claim_id'], 'supported': c['field'] == 'point.0', 'reason': 'Passage checked.'} for c in claims]})])

    result, _ = verify_claims(answer, [source], FunctionModel(review), contract=ResearchContract())
    text, _ = render_answer(result, [source], ResearchContract())
    assert len(result.knowledge.points) == 1
    assert '29' in text and 'always free' not in text and 'shortlist' not in text


def test_f2_country_scope_is_not_an_extra_destination_check():
    task = ResearchContract(subject='holiday destination', destination='Estonia', requirements=[
        Requirement(id='hotel', description='Named hotel for destination', scope='related')])
    assert not any(r.kind == 'location' and r.scope == 'primary' for r in task.requirements)
    errand = ResearchContract(activity='service', destination='Espoo', subject='recycling point')
    assert any(r.kind == 'location' and r.strength == 'hard' and 'Espoo' in r.description for r in errand.requirements)


def test_f3_captured_booking_budget_page_cannot_support_hotel_facts():
    source = PAGES['booking_budget']
    assert source_class(source) == 'seo'
    answer = GroundedAnswer(findings=[finding(source, 'Pärnu', 'Hotel Vesiroos')])
    result = apply_contract(answer, [source], ResearchContract(requirements=[Requirement(id='fact', description='Named hotel')]))
    assert result.findings[0].checks[0].basis == 'none'
    assert not result.findings[0].checks[0].evidence


def test_f4_multi_destination_spa_passes_do_not_rate_narva_as_budget_friendly():
    source = PAGES['narva_budget']
    answer = GroundedAnswer(findings=[finding(source, 'Narva', 'Budget-friendly spa packages')])
    task = ResearchContract(requirements=[Requirement(id='fact', description='Budget-friendly destination', strength='preference')])
    check = apply_contract(answer, [source], task).findings[0].checks[0]
    assert check.status == 'unknown' and not check.statement


def test_f5_unsupplied_paeva_villa_street_address_is_rejected_locally():
    source = PAGES['paeva_villa']
    answer = GroundedAnswer(findings=[finding(source, 'Päeva Villa', 'Lai 7')])
    result = apply_contract(answer, [source], ResearchContract(requirements=[Requirement(id='fact', description='Address')]))
    assert result.findings[0].checks[0].status == 'unknown'
    assert result.alignment_rejections[0]['value'] == 'Lai 7'


def test_f6_transhostel_compound_is_not_a_hotel():
    assert 'Transhostel Romantika' in PAGES['transhostel']['text']
    assert named_hotel_alternative('Transhostel Romantika')
    assert named_hotel_alternative('Seaside Külalistemaja')
    assert not named_hotel_alternative('Hotel and Hostel Central')


def test_f7_captured_outlet_offer_is_labelled_and_stock_gap_is_not_retried():
    source = deepcopy(PAGES['gigantti_outlet'])
    source['retrieved_at'] = source['observed_at'] = datetime.now(UTC).isoformat()
    answer = GroundedAnswer(findings=[finding(source, source['title'], '€559', identifier='price', kind='product')])
    identifier, (quote, _) = next((p, passage) for p, passage in citation_passages(source).items() if 'price:' in passage[0].casefold())
    answer.findings[0].checks[0].evidence = [Evidence(source_url=source['url'], quote=quote, passage_id=identifier)]
    task = ResearchContract(activity='product', requested_count=3, original_request='Find 3 laptops under €700; show stock',
                            budget=Budget(amount=700, operator='lt'))
    result = apply_contract(answer, [source], task)
    item = result.findings[0]
    assert item.condition == 'outlet'
    assert next(c for c in item.checks if c.requirement_id == 'price').status == 'met'
    assert next(c for c in item.checks if c.requirement_id == 'stock').refinement_excluded
    assert not any('stock' in (q.gap or '').casefold() for q in fallback_gap_queries(result.model_dump(), task, set(), [source]))
    text, _ = render_answer(result, [source], task)
    assert 'outlet' in text and 'stock shown at checkout' in text


def test_f8_missing_viable_hotel_generates_a_destination_specific_query():
    source = PAGES['transhostel']
    check = RequirementCheck(requirement_id='hotel', requirement='Named hotel for this destination', status='not_met',
        statement='Transhostel Romantika is a hostel alternative.', related_entity='Transhostel Romantika', scope='related',
        evidence=[Evidence(source_url=source['url'], quote=source['text'])])
    task = ResearchContract(subject='holiday destination', destination='Estonia', requested_count=1,
        requirements=[Requirement(id='hotel', description='Named hotel for this destination', scope='related')])
    answer = GroundedAnswer(findings=[Finding(name='Narva', source_url=source['url'], checks=[check])])
    queries = fallback_gap_queries(answer.model_dump(), task, set(), [source])
    assert any('Narva' in q.query and 'hotel' in q.query.casefold() for q in queries)


def test_f9_missing_preference_column_is_omitted_and_hard_gaps_are_grouped():
    source = PAGES['gigantti_outlet']
    item = finding(source, source['title'], '16 GB RAM', identifier='ram', kind='product')
    identifier, (quote, _) = next((p, passage) for p, passage in citation_passages(source).items() if '16 gb ddr4 ram' in passage[0].casefold())
    item.checks[0].evidence = [Evidence(source_url=source['url'], quote=quote, passage_id=identifier)]
    task = ResearchContract(activity='product', answer_format='table', requirements=[
        Requirement(id='ram', description='16 GB RAM'), Requirement(id='quiet', description='Prefer quiet fan', strength='preference')])
    text, _ = render_answer(GroundedAnswer(findings=[item]), [source], task)
    assert 'Prefer quiet fan' not in text
    assert 'Not found' not in text and 'Unverified' not in text
    assert text.count('To confirm') == 1


def test_f10_adult_is_a_price_variant_not_part_of_public_sauna_type():
    assert 'adult' not in PAGES['loyly_public']['text'].casefold()
    task = ResearchContract(activity='service', subject='adult public sauna session')
    assert next(r for r in task.requirements if r.kind == 'type').description == 'Matches the requested type: public sauna'
    assert task.variant_notes == ['adult price variant']


def test_f11_compact_schema_leaves_display_and_requirement_metadata_to_the_app():
    schema = LLMAnswer.model_json_schema()
    assert set(schema['$defs']['LLMCheck']['properties']) == {'requirement_id', 'status', 'value', 'evidence', 'reason'}
    source = PAGES['gigantti_outlet']
    identifier = next(p for p, (quote, _) in citation_passages(source).items() if '559' in quote)
    output = LLMAnswer.model_validate({'findings': [{'name': source['title'], 'source_url': source['url'], 'source_kind': 'product',
        'checks': [{'requirement_id': 'price', 'status': 'met', 'value': '€559', 'evidence': [identifier]}]}]})
    expanded = expand_llm_answer(output, [source], ResearchContract(activity='product', requirements=[Requirement(id='price', description='Price')]))
    assert expanded.findings[0].checks[0].evidence[0].source_url == source['url']


@pytest.mark.parametrize('label', ['€559/kk', '€559 (alv 0 %)', '€559 alv. 0 %', 'alv 0 %: €559', '€559 veroton'])
def test_consumer_budget_rejects_monthly_and_vat_excluded_prices(label):
    source = {'url': 'https://seller.fi/product/a', 'title': 'Laptop A', 'text': f'Laptop A Price: {label}',
              'retrieval': 'live', 'retrieved_at': datetime.now(UTC).isoformat()}
    answer = GroundedAnswer(findings=[finding(source, 'Laptop A', '€559', identifier='price', kind='product')])
    task = ResearchContract(activity='product', budget=Budget(amount=700, operator='lt'))
    assert apply_contract(answer, [source], task).findings[0].checks[0].status == 'unknown'


def test_related_product_text_cannot_borrow_identity_from_the_page_title():
    source = {'url': 'https://laptopa.example/product/a', 'title': 'Laptop A',
              'text': '# Laptop A\n\nRelated products: Laptop B has 32 GB RAM.'}
    answer = GroundedAnswer(findings=[finding(source, 'Laptop A', '32 GB RAM')])
    result = apply_contract(answer, [source], ResearchContract(requirements=[Requirement(id='fact', description='32 GB RAM')]))
    assert not result.findings[0].checks[0].evidence


def test_article_html_and_publisher_branding_do_not_establish_primary_ownership():
    shopping = ResearchContract(activity='product')
    assert classify('https://technews.example/news/laptop-a.html', 'Laptop A hands-on', 'We review this laptop.', shopping)[0] == 'off_target'
    assert classify('https://cityguide.example/sauna', 'Cityguide: Saunas in Helsinki', 'Our recommendations.', ResearchContract(activity='service'))[0] == 'secondary'


def test_alignment_numeric_boundaries_allow_punctuation_without_substring_matches():
    source = {'url': 'https://operator.example/', 'title': 'Operator', 'text': 'Capacity 17. Rate 559,00 €.'}
    evidence = [Evidence(source_url=source['url'], quote=source['text'])]
    assert alignment_ok('17', evidence, [source])
    assert alignment_ok('€559', evidence, [source])
    assert not alignment_ok('7', evidence, [source])


def test_cached_opening_hours_remain_reported_with_original_date():
    source = {'url': 'https://venuea.example/hours', 'title': 'Venue A', 'text': 'Venue A: Saturday 10:00–20:00.',
              'retrieval': 'cache', 'observed_at': '2026-11-12T12:00:00+00:00'}
    answer = GroundedAnswer(findings=[finding(source, 'Venue A', 'Saturday 10:00–20:00')])
    task = ResearchContract(activity='service', requirements=[Requirement(id='fact', description='Opening hours on Saturday')])
    check = apply_contract(answer, [source], task).findings[0].checks[0]
    assert check.status == 'unknown' and check.basis == 'reported'
    assert check.observed == source['observed_at']


def test_secondary_negative_is_reported_and_never_labelled_confirmed():
    source = {'url': 'https://guide.example/sauna', 'title': 'Sauna A', 'text': 'Sauna A is closed.'}
    item = finding(source, 'Sauna A', 'Closed')
    item.checks[0].status = 'not_met'
    task = ResearchContract(requirements=[Requirement(id='fact', description='Open')])
    check = apply_contract(GroundedAnswer(findings=[item]), [source], task).findings[0].checks[0]
    assert check.basis == 'reported'


def test_short_shopping_list_explicitly_searches_other_retailers():
    source = PAGES['gigantti_outlet']
    answer = GroundedAnswer(findings=[finding(source, source['title'], '16 GB RAM', kind='product')])
    task = ResearchContract(activity='product', subject='laptops', destination='Finland', requested_count=3)
    queries = fallback_gap_queries(answer.model_dump(), task, set(), [source])
    assert 'retailers other than gigantti.fi' in queries[0].query


def test_business_knowledge_uses_a_field_table_with_registry_links():
    source = {'url': 'https://ytj.fi/company/a', 'title': 'Registry', 'text': 'Business ID: 1234567-8.'}
    point = KnowledgePoint(statement=source['text'], evidence=[Evidence(source_url=source['url'], quote=source['text'])])
    task = ResearchContract(original_request='Give the business ID and registered address with official sources')
    text, _ = render_answer(GroundedAnswer(knowledge=KnowledgeAnswer(points=[point])), [source], task)
    assert '| Field | Information | Source |' in text
    assert '| Business ID |' in text and source['url'] in text
