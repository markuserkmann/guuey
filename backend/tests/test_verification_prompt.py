import json

from app.orchestration.verification import verification_prompt


def test_repeated_page_context_is_sent_once_without_losing_scoped_quotes():
    page = 'Hotel A is in Tartu. Hotel B is in Tallinn. ' * 200
    claims = [{'claim_id': index, 'candidate': name, 'evidence': [{
        'url': 'https://example.com/hotels', 'quote': name, 'context': name + ' address',
        'source_context': page}]} for index, name in enumerate(['Hotel A', 'Hotel B'])]
    encoded = verification_prompt(claims)
    compact = json.loads(encoded)
    first, second = [claim['evidence'][0] for claim in compact]
    assert first['source_context'] == page
    assert second['source_context_ref'] == first['source_context_id']
    assert 'source_context' not in second
    assert second['quote'] == 'Hotel B'
    assert second['context'] == 'Hotel B address'
    assert claims[1]['evidence'][0]['source_context'] == page
    assert len(encoded) < len(json.dumps(claims)) * 0.6


def test_different_source_versions_do_not_share_context_reference():
    claims = [{'evidence': [{'url': url, 'source_context': text}]} for url, text in [
        ('https://example.com/a', 'Old text'), ('https://example.com/a', 'New text'),
        ('https://example.com/b', 'Old text')]]
    compact = json.loads(verification_prompt(claims))
    assert len({c['evidence'][0]['source_context_id'] for c in compact}) == 3
    assert all('source_context' in c['evidence'][0] for c in compact)
