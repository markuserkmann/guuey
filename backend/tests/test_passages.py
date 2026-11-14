import pytest

from app.orchestration.grounding import passage_present
from app.orchestration.passages import find_passage


@pytest.mark.parametrize("markdown,quote", [
    ("Book **[Sauna A](https://example.fi/booking)** for *private hire*.", "Book Sauna A for private hire."),
    ("Sauna A is a floating sauna\nwith room for ten guests.", "Sauna A is a floating sauna with room for ten guests."),
    ("**Price:** 7&nbsp;€ / hour", "Price: 7 € / hour"),
    (r"Model A\-14 has **16 GB** RAM.", "Model A-14 has 16 GB RAM."),
    ("Booking<br>available online", "Booking available online"),
    ("| Memory | Size |\n|---|---|\n| RAM | **16 GB** |", "RAM: 16 GB"),
    ("The [booking calendar][booking] is open.\n\n[booking]: https://example.fi/calendar", "The booking calendar is open."),
])
def test_visible_markdown_quotes_match(markdown, quote):
    assert passage_present(quote, markdown)


@pytest.mark.parametrize("markdown,quote", [
    ("Laptop A costs €1900.", "900"),
    ("Laptop A costs 900.99 EUR.", "900"),
    ("The figure is 90%.", "90"),
    ("Linux is not supported.", "Linux supported"),
    ("[Read more](https://example.fi/ 'Linux supported')", "Linux supported"),
    ("| Model | RAM |\n|---|---|\n| Model A | 8 GB |\n| Model B | 16 GB |", "Model A 16 GB"),
    ("Sauna A.\n\nSauna B is available.", "Sauna A. Sauna B is available."),
    ("<script>Linux supported</script>", "Linux supported"),
])
def test_formatting_does_not_change_numbers_negations_or_combine_unrelated_rows(markdown, quote):
    assert not passage_present(quote, markdown)


def test_verification_context_keeps_table_headers_and_own_row():
    markdown = "# Sauna A\n\n| Package | Price / hour |\n|---|---|\n| Private hire | **70 €** |\n| Shared seat | 7 € |"
    match = find_passage("70 €", markdown)
    assert match is not None
    assert "Sauna A" in match.context
    assert "Price / hour" in match.context
    assert "Private hire" in match.context
    assert "Shared seat" not in match.context


def test_verbatim_list_markers_match_without_losing_candidate_context():
    from app.orchestration.passages import find_passage
    source = '# Laptop A\n\n- Price: 637,00\u00a0€\n- Sisäinen RAM-muisti (GB): 16\n'
    price = find_passage('- Price: 637,00 €', source)
    assert price is not None and 'Laptop A' in price.context
    assert find_passage('- Sisäinen RAM-muisti (GB): 16', source)
    assert find_passage('- Price: 37,00 €', source) is None
    assert find_passage('- Price: 637,00 € - Sisäinen RAM-muisti (GB): 16', source) is None


def test_identical_prices_in_different_sections_keep_distinct_references():
    from app.orchestration.passages import citation_passages

    source = {'url': 'https://example.fi/sauna', 'text': '# Sauna A\n\n## Public session\n\n€20 per person.\n\n## Private hire\n\n€20 per person.'}
    prices = [(identifier, quote, context) for identifier, (quote, context) in citation_passages(source).items()
              if quote == '€20 per person.']
    assert len(prices) == 2
    assert prices[0][0] != prices[1][0]
    for identifier, quote, context in prices:
        match = find_passage(quote, source['text'], passage_id=identifier, source_url=source['url'])
        assert match and match.context == context
    assert find_passage('€200 per person.', source['text'], passage_id=prices[1][0], source_url=source['url']) is None
    assert find_passage('€20 per person.', source['text'], passage_id=prices[1][0], source_url='https://other.fi/') is None
