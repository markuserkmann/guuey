import pytest

from app.browser.guard import is_card_field, is_final_control, secret_allowed


@pytest.mark.parametrize('label', ['Place order', 'Pay now', 'Send', 'Delete', 'Tilaa', 'Maksa', 'Vahvista tilaus',
    'Beställ', 'Betala', 'Telli', 'Kinnita', 'Bestellen', 'Zahlungspflichtig bestellen', 'Buchen'])
def test_final_action_names(label):
    assert is_final_control(label, 'https://example.com/form', 'button')


def test_checkout_submit_and_custom_patterns():
    assert is_final_control('Continue', 'https://example.com/checkout', 'submit')
    assert is_final_control('Commit', 'https://example.com/form', 'button', 'commit')
    assert not is_final_control('Search', 'https://example.com/search', 'submit')
    assert not is_final_control('Add to cart', 'https://example.com/menu', 'button')


@pytest.mark.parametrize('attrs', [{'autocomplete': 'section-payment cc-number'}, {'label': 'Card number'}, {'name': 'CVV'}, {'placeholder': 'Expiry'}])
def test_card_field_labels(attrs):
    assert is_card_field(attrs)


def test_secret_domain_scope():
    assert secret_allowed('https://account.example.com/login', 'example.com')
    assert not secret_allowed('https://example.com.other.com/login', 'example.com')
    assert not secret_allowed('file:///etc/passwd', 'example.com')
