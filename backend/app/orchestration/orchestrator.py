from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from time import monotonic
import re
from urllib.parse import urlsplit

from app.chat import generate_reply, fill_gaps
from app.model_calls import model_call_events
from app.orchestration.classifier import TurnIntent, classify_turn
from app.orchestration.planner import plan_searches
from app.orchestration.contracts import ResearchContract, SearchPlan
from app.orchestration.interpretation import preserve_request, query_key
from app.orchestration.refinement import prioritize_refinement_queries
from app.orchestration.evidence_budget import off_target_source, offer_source
from app.orchestration.direct_actions import gap_actions
from app.research import SearchUnavailable, diverse_sources, extract_pages, live_source, offer_links, search, source_domain, urls_in_message
from app.schemas import ChatTurn
from app.research_modes import current_profile, ResearchDeadline
from app.source_cache import policy_sources, usable_policy
from app.source_quality import source_class, QUALITY_RANK


@dataclass
class TurnResult:
    reply: str
    sources: list[dict[str, str]]
    debug: dict


class RefinementComplete(Exception):
    pass


def run_turn(message: str, history: list[ChatTurn], *, search_requested: bool, seed_sources: list[dict] | None = None, contract: ResearchContract | None = None, verification_cache: dict | None = None, attempted_queries: set[str] | None = None, prior_answer: dict | None = None, retrieval_state: dict | None = None, fresh_required: bool = False) -> TurnResult:
    started = monotonic()
    force_fresh = fresh_required
    intent = classify_turn(search_requested=search_requested)
    sources: list[dict[str, str]] = []
    plan_debug = None
    search_failures = extraction_failures = reused_pages = 0
    attempted_queries = attempted_queries if attempted_queries is not None else set()
    executed_queries = []
    policy_pages = 0
    offer_pages = 0
    retrieval_state = retrieval_state if retrieval_state is not None else {}
    crawled = retrieval_state.setdefault('crawled', set())
    policy_attempted = retrieval_state.setdefault('policy_domains', set())
    contents_calls = 0
    live_pages_requested = 0
    direct_seconds = 0
    handled = set()
    direct_trace = []

    def fetch(urls, **options):
        nonlocal contents_calls, live_pages_requested
        if force_fresh:
            options.update(fresh=True, bypass_cache=True)
        identity = (options.get('fresh', False), tuple(options.get('subpage_targets', [])))
        urls = list(dict.fromkeys(url for url in urls if (url, identity) not in crawled))
        if options.get('fresh'):
            urls = urls[:max(0, current_profile().livecrawl_pages - live_pages_requested)]
        if not urls or contents_calls >= 2:
            return []
        contents_calls += 1
        crawled.update((url, identity) for url in urls)
        if options.get('fresh'):
            live_pages_requested += len(urls)
        pages = extract_pages(urls, limit=len(urls), **options)
        if pages and all(page.get('cache_hit') for page in pages):
            contents_calls -= 1
            if options.get('fresh'):
                live_pages_requested -= len(urls)
        return pages

    if contract and contract.subject and re.search(r"\b(?:and|with)\s+hotels?\b", contract.subject, re.I):
        contract = preserve_request(contract, contract.original_request or message)
    urls = urls_in_message(message)
    page_question = bool(urls and re.search(r'\b(read|according to|what does)\b', message, re.I)
                         and not re.search(r'\b(compare|recommend|find|shortlist)\b', message, re.I))
    if intent == TurnIntent.SEARCH:
        direct_only = False
        if prior_answer and contract:
            direct_started = monotonic()
            if contract.shopping:
                domains = {source_domain(f['source_url']) for f in prior_answer.get('findings', []) if f.get('source_url')}
                cached_policies = [] if force_fresh else [page for domain in domains for page in policy_sources(domain)]
                known = {s['url']: s for s in seed_sources or []}
                for page in cached_policies:
                    known.setdefault(page['url'], page)
                seed_sources = list(known.values())
            actions, pending_gaps, needs_candidates = gap_actions(prior_answer, contract, seed_sources or [])
            delivery_ids = {r.id for r in contract.requirements if r.kind == 'delivery'}
            for finding in prior_answer.get('findings', []):
                domain = source_domain(finding.get('source_url') or '')
                reusable = [s for s in seed_sources or [] if source_domain(s['url']) == domain and usable_policy(s)]
                if reusable:
                    sources.extend(s for s in reusable if s not in sources)
                    handled.update((finding['name'], check.get('requirement_id')) for check in finding.get('checks', [])
                                   if check.get('requirement_id') in delivery_ids and check.get('status') != 'met')
            for kind in ('offer', 'policy', 'service'):
                selected = {url: gaps for (action, url), gaps in actions.items() if action == kind}
                if kind == 'policy':
                    selected = {url: gaps for url, gaps in selected.items() if source_domain(url) not in policy_attempted}
                if not selected:
                    continue
                options = {'fresh': kind == 'offer', 'focus': message}
                if kind == 'policy':
                    options['subpage_targets'] = ['shipping and delivery policy', f'delivery to {contract.destination}', 'toimitusehdot', 'toimitustavat']
                elif kind == 'service':
                    options['subpage_targets'] = ['book', 'varaa', 'prices', 'hinnasto']
                try:
                    if kind == 'policy':
                        policy_attempted.update(source_domain(url) for url in selected)
                    pages = fetch(list(selected)[:8], **options)
                    sources.extend(pages)
                    if kind == 'policy':
                        policy_pages += len(pages)
                    for url, gaps in selected.items():
                        if any(source_domain(p['url']) == source_domain(url) for p in pages):
                            handled.update(gaps)
                    direct_trace.append({'kind': kind, 'urls': list(selected), 'returned': len(pages)})
                except SearchUnavailable:
                    extraction_failures += 1
            direct_only = bool(handled and pending_gaps <= handled and not needs_candidates)
            direct_seconds = monotonic() - direct_started
        planning_request = "Original request:\n" + message
        if history:
            planning_request += "\nRecent context; the current request above overrides older constraints:\n" + "\n".join(f"{turn.role}: {turn.content}" for turn in history[-12:])[-16000:]
        if attempted_queries:
            planning_request += "\nAlready attempted searches (do not repeat):\n" + "\n".join(sorted(attempted_queries))[-8000:]
        if direct_only:
            plan = SearchPlan(contract=contract, queries=[])
            plan_debug = {'direct_actions_only': True}
        elif page_question:
            plan = SearchPlan(contract=ResearchContract(activity='research', original_request=message, objective=message), queries=[])
            contract = plan.contract
            history = []
        else:
            if handled:
                planning_request += '\nAlready retrieved directly; plan only OTHER gaps:\n' + str(sorted(handled))
            plan, plan_debug = plan_searches(planning_request, contract)
        contract = contract or preserve_request(plan.contract, message)
        if plan_debug:
            plan_debug["effective_contract"] = contract.model_dump()
        started = monotonic()
        fresh_required = fresh_required or contract.activity in {"product", "service"} or any(r.kind in {"price", "availability"} for r in contract.requirements)
        if urls:
            sources = fetch(urls, fresh=fresh_required or page_question, focus=message)
        else:
            plan.queries = [q for q in plan.queries if query_key(q) not in attempted_queries]
            if prior_answer and not contract.clarification_question and not direct_only:
                plan.queries = prioritize_refinement_queries(plan.queries, prior_answer, contract, attempted_queries, seed_sources)
                if plan_debug is not None:
                    plan_debug["prioritized_refinement_queries"] = [q.model_dump() for q in plan.queries]
            if not plan.queries and not sources:
                if not seed_sources and not attempted_queries:
                    question = contract.clarification_question
                    reply = question or "I couldn't identify a useful evidence search for this request. Please specify the item, service or question to research."
                    return TurnResult(reply=reply, sources=[], debug={"intent": intent.value,
                        "search_plan": plan_debug, "research_contract": contract.model_dump(),
                        "has_gaps": True, "executed_queries": [], "next_action": {
                            "kind": "clarification" if question else "none", "text": question or "", "gaps": []},
                        "stop_reason": "no_useful_new_query"})
                raise RefinementComplete("No new evidence query remains.")
            collected: dict[str, dict[str, str]] = {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = []
                for query in plan.queries:
                    attempted_queries.add(query_key(query))
                    executed_queries.append(query.model_dump())
                    options = {"objective": query.objective, "country": contract.country_code}
                    for field in ("include_domains", "exclude_domains", "start_published_date", "end_published_date", "category"):
                        if getattr(query, field):
                            options[field] = getattr(query, field)
                    if query.search_type != "auto":
                        options["search_type"] = query.search_type
                    futures.append(pool.submit(copy_context().run, search, query.query, **options))
                batches = []
                failures = []
                for future in futures:
                    try:
                        batches.append(future.result())
                    except SearchUnavailable as exc:
                        failures.append(exc)
                        search_failures += 1
                        continue
                for index in range(max((len(batch) for batch in batches), default=0)):
                    for batch in batches:
                        if index < len(batch):
                            source = batch[index]
                            collected.setdefault(source["url"], source)
            if not collected and plan.queries and not sources:
                if failures:
                    raise failures[0]
                raise SearchUnavailable("Exa did not return readable search results for this request.")
            sources = diverse_sources([*sources, *collected.values()], 24 if contract.shopping else 30)
            cached = {} if force_fresh else {source["url"]: source for source in (seed_sources or [])
                      if (live_source(source) if fresh_required else source.get("retrieval") == "page")}
            reused_pages = sum(source['url'] in cached for source in sources)
            sources = [cached.get(source['url'], source) for source in sources]
            sources.sort(key=lambda source: (off_target_source(source, contract), QUALITY_RANK[source_class(source, contract)]))
            if fresh_required:
                unresolved = {finding.get('source_url') for finding in (prior_answer or {}).get('findings', [])
                              if any(check.get('status') == 'unknown' and check.get('requirement_id') in
                                     {r.id for r in contract.requirements if r.kind in {'price', 'stock', 'availability'}}
                                     for check in finding.get('checks', []))}
                pending = [source for source in sources if source['url'] not in cached
                           and (force_fresh or offer_source(source, contract))
                           and (force_fresh or not prior_answer or source['url'] in unresolved)]
            else:
                pending = [source for source in sources if not source.get('text', '').strip()]
            try:
                pages = fetch([source['url'] for source in pending[:8]], fresh=fresh_required, focus=message)
            except SearchUnavailable:
                extraction_failures += 1
                pages = []
            page_by_url = {page['url']: page for page in pages}
            page_by_url.update({page['requested_url']: page for page in pages if page.get('requested_url')})
            sources = [page_by_url.get(source['url'], source) for source in sources]
            known_urls = {source['url'] for source in sources}
            sources.extend(page for page in pages if page['url'] not in known_urls)
        if contract.shopping and prior_answer:
            by_url = {source['url']: source for source in [*(seed_sources or []), *sources]}
            linked_offers = dict.fromkeys(url for lead in prior_answer.get('discovery_leads', [])
                if lead.get('name') and lead.get('source_url') in by_url
                for url in offer_links(by_url[lead['source_url']], lead['name'])
                if not off_target_source({**by_url[lead['source_url']], 'url': url}, contract)
                if url not in by_url or not live_source(by_url[url]))
            if linked_offers:
                try:
                    offers = fetch(list(linked_offers)[:8], fresh=True, focus=message)
                    offer_pages = len(offers)
                    offer_urls = {offer['url'] for offer in offers}
                    sources = offers + [source for source in sources if source['url'] not in offer_urls]
                except SearchUnavailable:
                    extraction_failures += 1
        if contract.shopping and contract.destination:
            delivery_ids = {r.id for r in contract.requirements if r.kind == 'delivery'}
            sellers = dict.fromkeys(
                f"{urlsplit(finding['source_url']).scheme}://{urlsplit(finding['source_url']).netloc}/"
                for finding in (prior_answer or {}).get('findings', []) if finding.get('source_url')
                and any(check.get('requirement_id') in delivery_ids and check.get('status') == 'unknown'
                        and (finding['name'], check.get('requirement_id')) not in handled
                        for check in finding.get('checks', [])))
            if not prior_answer:
                sellers = dict.fromkeys(f"{urlsplit(s['url']).scheme}://{urlsplit(s['url']).netloc}/"
                                        for s in sources if offer_source(s, contract))
            known_policy_domains = {source_domain(s['url']) for s in [*(seed_sources or []), *sources] if usable_policy(s)}
            cached_policies = [] if force_fresh else [page for homepage in sellers for page in policy_sources(source_domain(homepage))
                               if source_domain(homepage) not in known_policy_domains]
            sources.extend(page for page in cached_policies if page['url'] not in {s['url'] for s in sources})
            known_policy_domains.update(source_domain(page['url']) for page in cached_policies)
            targets = ['shipping and delivery policy', f'delivery to {contract.destination}', 'store locations and collection']
            if contract.country_code == 'FI':
                targets.extend(['toimitusehdot', 'toimitustavat'])
            try:
                homes = [homepage for homepage in list(sellers)[:3]
                         if source_domain(homepage) not in known_policy_domains | policy_attempted]
                policy_attempted.update(source_domain(homepage) for homepage in homes)
                policies = fetch(homes,
                                 fresh=False, focus=message, subpage_targets=targets)
                policy_pages += len(policies)
                policy_urls = {policy['url'] for policy in policies}
                sources = policies + [source for source in sources if source['url'] not in policy_urls]
            except SearchUnavailable:
                extraction_failures += 1
    if seed_sources:
        combined = {source["url"]: source for source in sources}
        for source in seed_sources:
            combined.setdefault(source["url"], source)
        priority_urls = set()
        if prior_answer:
            for finding in prior_answer.get("findings", []):
                priority_urls.add(finding.get("source_url"))
                for check in finding.get("checks", []):
                    priority_urls.update(e.get("source_url") for e in check.get("evidence", []))
            for point in (prior_answer.get('knowledge') or {}).get('points', []):
                priority_urls.update(e.get('source_url') for e in point.get('evidence', []))
        prioritized = [s for url, s in combined.items() if url in priority_urls]
        others = diverse_sources([s for url, s in combined.items() if url not in priority_urls], 30)
        sources = prioritized + others[:max(0, 30 - len(prioritized))]
    excluded_non_live_sources = 0
    if force_fresh:
        live_pages = [source for source in sources if live_source(source) and not source.get('cache_hit')]
        excluded_non_live_sources = len(sources) - len(live_pages)
        sources = live_pages
        if not sources:
            raise SearchUnavailable("No live source pages could be retrieved for this check. Cached evidence was not used.")
    retrieval_seconds = round(monotonic() - started + direct_seconds, 2)
    retrieval = {"sources": len(sources), "domains": len({source_domain(s["url"]) for s in sources}),
                 "live_pages": sum(live_source(s) for s in sources), "elapsed_seconds": retrieval_seconds,
                 "cache_hits": sum(bool(s.get('cache_hit')) for s in sources),
                 "reused_pages": reused_pages, "search_failures": search_failures,
                 "extraction_failures": extraction_failures, "policy_pages": policy_pages, "offer_pages": offer_pages, "contents_calls": contents_calls}
    if force_fresh:
        retrieval.update(cache_bypassed=True, excluded_non_live_sources=excluded_non_live_sources)
    observer = model_call_events.get()
    if observer and search_requested:
        observer({"stage": "retrieval", "status": "completed", "search_plan": plan_debug,
                  "retrieval": retrieval, "sources": [{k: v for k, v in source.items() if k != "text"} for source in sources]})
    fallback = None
    if prior_answer and contract:
        try:
            reply, debug = fill_gaps(message, sources, contract, prior_answer, verification_cache)
        except Exception as exc:
            from pydantic_ai.exceptions import ModelAPIError
            if isinstance(exc, (ModelAPIError, ResearchDeadline)):
                raise
            fallback = type(exc).__name__
    if not prior_answer or not contract or fallback:
        reply, debug = generate_reply(message, history, sources, contract=contract, verification_cache=verification_cache, prior_answer=prior_answer)
    if fallback:
        debug['gap_filling_fallback'] = fallback
    debug['direct_actions'] = direct_trace
    debug["intent"] = intent.value
    debug["search_plan"] = plan_debug
    debug["research_contract"] = contract.model_dump() if contract else None
    debug["retrieval"] = retrieval
    debug["executed_queries"] = executed_queries
    return TurnResult(reply=reply, sources=sources, debug=debug)
