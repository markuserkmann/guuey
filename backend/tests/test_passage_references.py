from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck, matching_evidence
from app.orchestration.passages import citation_passages
from app.orchestration.passages import render_source_passages
from app.orchestration.verification import resolve_source_ids
from app.research import stable_source_id


def test_passage_references_resolve_only_within_their_source():
    source = {'url': 'https://hotel.example/a', 'title': 'Hotel A', 'text': '# Hotel A\n\nHotel A is located in Pärnu.\n\nRooms start from €60 in winter.'}
    other = {'url': 'https://hotel.example/b', 'title': 'Hotel B', 'text': '# Hotel B\n\nHotel B is located in Tartu.'}
    refs = citation_passages(source)
    identifier = next(key for key, (quote, _) in refs.items() if 'located' in quote)
    evidence = Evidence(source_url=stable_source_id(source['url']), quote=identifier)
    output = GroundedAnswer(findings=[Finding(name='Hotel A', source_url=evidence.source_url, checks=[
        RequirementCheck(requirement='Located in Pärnu', status='met', statement='Hotel A is in Pärnu', evidence=[evidence])])])
    resolved = resolve_source_ids(output, [other, source])
    actual = resolved.findings[0].checks[0].evidence[0]
    assert actual.source_url == source['url']
    assert actual.quote == 'hotel a is located in pärnu.'
    assert matching_evidence([actual], [source])
    assert citation_passages(source) == refs
    wrong = output.model_copy(deep=True)
    wrong.findings[0].checks[0].evidence = [Evidence(source_url=other['url'], quote=identifier)]
    resolve_source_ids(wrong, [source, other])
    assert not matching_evidence(wrong.findings[0].checks[0].evidence, [source, other])


def test_same_text_on_other_pages_has_different_passage_ids():
    a={'url':'https://example.com/a','text':'Rooms from €60.'}
    b={'url':'https://example.com/b','text':a['text']}
    assert not set(citation_passages(a)) & set(citation_passages(b))


def test_labelled_passage_uses_source_text_and_discards_model_suffix():
    source = {'url': 'https://example.com/a', 'text': 'Hotel A is in Tartu.'}
    identifier = next(iter(citation_passages(source)))
    answer = GroundedAnswer(findings=[Finding(name='Hotel A', source_url=source['url'], checks=[
        RequirementCheck(requirement='Location', status='met', statement='Hotel A is in Tartu.',
                         evidence=[Evidence(source_url=source['url'], quote=f'[{identifier}] invented extra claims')])])])
    resolve_source_ids(answer, [source])
    assert answer.findings[0].checks[0].evidence[0].quote == 'hotel a is in tartu.'


def test_rendering_preserves_ids_without_repeating_section_context():
    import hashlib
    from app.orchestration.passages import passages, normalize_text

    source = {'url': 'https://seller.fi/product/a', 'text': '# Laptop A\n\n## Specifications\n\n| Feature | Value |\n|---|---|\n| RAM | 16 GB |\n| SSD | 512 GB |\n\n## Offer\n\nPrice €649.\n\nIn stock.'}
    before = {'P_' + hashlib.sha256((source['url'] + '\n' + p.variants[0] + '\n' + normalize_text(p.context)).encode()).hexdigest()[:16]
              for p in passages(source['text']) if p.variants[0]}
    rendered = render_source_passages(source)
    assert set(citation_passages(source)) == before
    assert all(rendered.count(identifier) == 1 for identifier in before)
    assert rendered.count('Laptop A') == 1
    assert rendered.count('Specifications') == 1
    row = next(identifier for identifier, (quote, _) in citation_passages(source).items() if '16 gb' in quote)
    selected = render_source_passages(source, {row})
    assert 'Feature | Value' in selected and '16 GB' in selected
    assert '512 GB' not in selected


def test_compact_rendering_of_captured_laptop_page():
    import json
    from pathlib import Path

    source = json.loads((Path(__file__).parent / 'fixtures' / 'laptop-page.json').read_text())
    assert len(render_source_passages(source)) <= 1.2 * len(source['text'])


def test_setext_headings_are_rendered_once():
    source = {'url': 'https://example.fi/a', 'text': 'Laptop A\n========\n\nSpecifications\n--------------\n\n16 GB RAM.\n\n512 GB SSD.'}
    rendered = render_source_passages(source)
    assert rendered.count('Laptop A') == 1
    assert rendered.count('Specifications') == 1
    assert all(rendered.count(identifier) == 1 for identifier in citation_passages(source))
