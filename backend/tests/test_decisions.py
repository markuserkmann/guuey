import httpx
import pytest

from app.orchestration.decisions import DecisionUnavailable, DecisionUncertain, decide


@pytest.mark.parametrize('confidence,accepted', [(0.95, True), (0.5, False)])
def test_decision_confidence_and_usage(monkeypatch, confidence, accepted):
    monkeypatch.setenv('GEMINI_API_KEY', 'fixture-key')

    def handler(request):
        assert request.url.path == '/v1beta/decisions'
        return httpx.Response(200, json={
            'answers': {
                'workflow': {'choice': 'research', 'confidence': confidence},
                'deep': {'noul': 0.95, 'confidence': confidence},
            },
            'usage': {'total_input_tokens': 80, 'total_output_tokens': 0},
        })

    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if not accepted:
        with pytest.raises(DecisionUncertain) as error:
            decide('Find ten laptops')
        assert error.value.decision.confidence == confidence
    else:
        result = decide('Find ten laptops')
        assert result.workflow == 'research'
        assert result.deep_recommended
        assert result.usage['total_output_tokens'] == 0


def test_missing_key_needs_fallback(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    with pytest.raises(DecisionUnavailable):
        decide('hello')


def test_uncertain_depth_does_not_discard_confident_workflow(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'fixture-key')
    def handler(request):
        return httpx.Response(200, json={'answers': {
            'workflow': {'choice': 'research', 'confidence': 0.95},
            'deep': {'noul': 0.95, 'confidence': 0.3},
        }})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    result = decide('Find laptop prices')
    assert result.workflow == 'research' and result.confidence == 0.95
    assert not result.deep_recommended
