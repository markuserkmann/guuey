"""Map known evidence gaps to page retrieval before planning another search."""
from urllib.parse import urlsplit

from app.orchestration.evidence_budget import offer_source
from app.research import live_source, source_domain
from app.source_cache import usable_policy


def gap_actions(answer, contract, sources):
    by_url = {s['url']: s for s in sources}
    requirements = {r.id: r for r in contract.requirements}
    actions = {}
    pending = set()
    hard_failure = False
    for finding in answer.get('findings', []):
        url = finding.get('source_url')
        for check in finding.get('checks', []):
            if check.get('refinement_excluded'):
                continue
            if check.get('status') == 'met':
                continue
            key = (finding['name'], check.get('requirement_id'))
            pending.add(key)
            requirement = requirements.get(check.get('requirement_id'))
            if not requirement or not url:
                continue
            if check.get('status') == 'not_met' and requirement.strength == 'hard':
                hard_failure = True
                continue
            if contract.shopping and requirement.kind in {'price', 'stock', 'availability'}:
                source = by_url.get(url)
                if source and offer_source(source, contract) and not live_source(source):
                    actions.setdefault(('offer', url), set()).add(key)
            elif contract.shopping and requirement.kind == 'delivery':
                parts = urlsplit(url)
                home = f'{parts.scheme}://{parts.netloc}/'
                if not any(s['url'] != url and source_domain(s['url']) == source_domain(url) and usable_policy(s) for s in sources):
                    actions.setdefault(('policy', home), set()).add(key)
            elif contract.activity == 'service' and requirement.kind in {'booking', 'price'}:
                actions.setdefault(('service', url), set()).add(key)
    shortfall = bool(contract.requested_count and len(answer.get('findings', [])) < contract.requested_count)
    return ({} if hard_failure else actions), pending, hard_failure or shortfall
