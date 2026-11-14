import re

from app.orchestration.contracts import Budget, ResearchContract
from app.orchestration.evidence_budget import budget_evidence, off_target_source
from app.orchestration.passages import citation_passages


def laptop():
    return {'url': 'https://seller.fi/product/laptop', 'title': 'Laptop A',
            'text': '# Laptop A\n\n' + '\n\n'.join(f'[Navigation {i}](https://seller.fi/nav/{i})' for i in range(200))
            + '\n\n## Laptop A current offer\n\n| Configuration | Price |\n|---|---|\n| 16 GB RAM / 512 GB SSD | €649 |\n\nIn stock.\n\nShips to Finland.'}


def task():
    return ResearchContract(activity='product', subject='laptop', destination='Finland', country_code='FI',
                            budget=Budget(amount=700, operator='lt'))


def test_budget_keeps_price_and_table_context_and_drops_navigation():
    source = laptop()
    rendered, trace = budget_evidence([source], task(), per_source=1800, global_budget=1800)
    text = rendered[source['url']]
    assert '€649' in text and 'Configuration | Price' in text
    assert 'Navigation 150' not in text
    assert len(text) <= 1800
    assert trace[0]['kept_passages'] < trace[0]['total_passages']
    assert set(re.findall(r'\[(P_[a-f0-9]+)\]', text)) <= set(citation_passages(source))


def test_global_budget_and_focus_exemption():
    sources = [{**laptop(), 'url': f'https://seller.fi/product/{i}'} for i in range(10)]
    rendered, trace = budget_evidence(sources, task(), per_source=1800, global_budget=4000)
    assert sum(map(len, rendered.values())) <= 4000
    focused, trace = budget_evidence(sources, task(), global_budget=4000, focus={sources[0]['url']})
    assert trace[0]['kept_chars'] == trace[0]['total_chars']
    assert 'Navigation 150' in focused[sources[0]['url']]
    assert all(not text for url, text in focused.items() if url != sources[0]['url'])


def test_off_target_pages_are_demoted_but_retained():
    source = {**laptop(), 'url': 'https://seller.it/product/laptop'}
    assert off_target_source(source, task())
    rendered, trace = budget_evidence([source], task())
    assert 0 < len(rendered[source['url']]) <= 1800
    assert trace[0]['off_target']
