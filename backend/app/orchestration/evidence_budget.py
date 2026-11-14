"""Select prompt passages while retaining the complete citation catalog for verification."""
import re

from app.orchestration.passages import indexed_passages, render_source_passages, visible
from app.research import category_source, comparison_source, source_domain
from app.source_quality import source_class, QUALITY_RANK

STOP_WORDS = frozenset('a an and are as at be by for from has have in is it of on or that the their this to with its exact currently requested requirement matches located provides information available'.split())
KIND_TERMS = {
    'price': '€ eur hinta price alkaen from cost rate prices hinnasto',
    'stock': 'varastossa in stock saatavilla availability',
    'availability': 'available availability saatavilla calendar dates',
    'delivery': 'toimitus delivery shipping nouto finland suomi suomeen',
    'booking': 'book booking varaa reservation reservations',
    'fact': 'min minutes hours duration tuntia',
}


def terms(text):
    return set(re.findall(r'\w{2,}|€', text.casefold())) - STOP_WORDS


def contract_terms(contract):
    if not contract:
        return set()
    text = ' '.join(filter(None, [contract.subject, contract.destination, contract.anchor,
                    *contract.exact_entities, *[r.description for r in contract.requirements],
                    contract.budget.currency if contract.budget else None]))
    lexicon = ' '.join(KIND_TERMS.get(r.kind, '') for r in contract.requirements)
    return terms(text + ' ' + lexicon)


def off_target_source(source, contract):
    if source_class(source, contract) in {"seo", "off_target"}:
        return True
    if not contract:
        return False
    url = source['url']
    domain = source_domain(url)
    text = source.get('title', '') + ' ' + source.get('text', '')[:6000]
    if contract.shopping:
        if comparison_source(url) or re.search(r'trustpilot\.|/(?:reviews?|buying-guides?|best-laptops)(?:/|$)', url, re.I):
            return True
        if re.search(r'\b(?:best laptops|buying guide|laptop reviews)\b', source.get('title', ''), re.I):
            return True
        if any(r.kind == 'delivery' and r.strength == 'hard' for r in contract.requirements):
            country = (contract.country_code or '').casefold()
            suffix = domain.rsplit('.', 1)[-1]
            if country and suffix in {'fi', 'ee', 'de', 'fr', 'it', 'es', 'se', 'no', 'dk', 'uk', 'us', 'nl', 'pl'} and suffix != country:
                return True
    requested = terms(' '.join(filter(None, [contract.subject, *contract.exact_entities,
                                            *[r.description for r in contract.requirements if r.kind == 'fact']])))
    return bool(requested and not requested.intersection(terms(text)))


def offer_source(source, contract):
    if off_target_source(source, contract) or comparison_source(source['url']) or category_source(source['url']):
        return False
    from urllib.parse import urlsplit
    path = urlsplit(source['url']).path
    if contract.shopping:
        return bool(re.search(r'/(?:products?|tuote|kauppa/tuote|p|dp|item|recommerce/forsale/item|kannettava-tietokone)/|/\d[^/]*[-/]|/c/\d+[^/]*/[^/]+|\.html$', path, re.I))
    return contract.activity == 'service' and bool(path.strip('/'))


def focus_urls(prior_answer):
    result = set()
    for finding in (prior_answer or {}).get('findings', []):
        result.add(finding.get('source_url'))
        for check in finding.get('checks', []):
            if check.get('status') != 'met':
                result.update(e.get('source_url') for e in check.get('evidence', []))
    return result - {None}


def passage_score(passage, wanted, names):
    text = visible(passage.raw)
    score = 3 * len(wanted.intersection(terms(text)))
    score += 4 * len(wanted.intersection(terms(' '.join(passage.headings) + ' ' + passage.table_header)))
    score += 8 * bool(re.search(r'(?:€|eur|[$£])\s*\d|\d\s*(?:€|eur)\b', text, re.I))
    score += 10 * any(name in text for name in names if name)
    if re.fullmatch(r'\s*(?:[-*+]\s*)?(?:\[[^\]]*\]\([^)]*\)\s*)+', passage.raw):
        score -= 40
    if re.search(r'cookie|consent|privacy preferences|accept all|eväste', text, re.I):
        score -= 50
    if len(text) < 35 and not re.search(r'\d', text):
        score -= 4
    return score


def select_passages(source, wanted, names, budget):
    entries = list(indexed_passages(source).items())
    if budget <= 0:
        return set()
    scores = [passage_score(p, wanted, names) for _, p in entries]
    identity = [i for i, (_, p) in enumerate(entries) if scores[i] > -20][:3]
    order = list(dict.fromkeys([*identity, *sorted(range(len(entries)), key=lambda i: scores[i], reverse=True)]))
    chosen = set()
    used = 0
    for index in order:
        if index not in identity and scores[index] <= 0:
            continue
        for candidate in (index, index - 1, index + 1):
            if candidate < 0 or candidate >= len(entries):
                continue
            identifier, passage = entries[candidate]
            if identifier in chosen or (candidate != index and scores[candidate] < -20):
                continue
            cost = len(passage.raw) + len(identifier) + 5 + sum(len(h) + 2 for h in passage.headings) + len(passage.table_header) + 2
            if used + cost <= budget:
                chosen.add(identifier)
                used += cost
    return chosen


def budget_evidence(sources, contract, prior_answer=None, *, per_source=16000, global_budget=320000, focus=None):
    wanted = contract_terms(contract)
    names = [visible(f['name']) for f in (prior_answer or {}).get('findings', []) if f.get('name')]
    names += [visible(f['name']) for f in (prior_answer or {}).get('discovery_leads', []) if f.get('name')]
    focused = focus_urls(prior_answer) if focus is None else set(focus)
    ranked = sorted(enumerate(sources), key=lambda item: (off_target_source(item[1], contract),
                    QUALITY_RANK[source_class(item[1], contract)], item[0]))
    rendered, debug = {}, []
    # Focus pages are deliberately exempt; their entire text can be needed to resolve a gap.
    remaining = max(0, global_budget - sum(len(render_source_passages(s)) for s in sources if s['url'] in focused))
    for _, source in ranked:
        full = render_source_passages(source)
        total = len(indexed_passages(source))
        is_focus = source['url'] in focused and not off_target_source(source, contract)
        if is_focus:
            text = full
            kept = total
        else:
            allowance = min(remaining, 1800 if off_target_source(source, contract) else per_source)
            if len(full) <= allowance:
                text, kept = full, total
            else:
                selected = select_passages(source, wanted, names, allowance)
                text, kept = render_source_passages(source, selected), len(selected)
            remaining -= len(text)
        rendered[source['url']] = text
        debug.append({'url': source['url'], 'total_chars': len(full), 'kept_chars': len(text),
                      'total_passages': total, 'kept_passages': kept, 'focus': is_focus,
                      'off_target': off_target_source(source, contract)})
        debug[-1]['source_class'] = source_class(source, contract)
    return rendered, debug
