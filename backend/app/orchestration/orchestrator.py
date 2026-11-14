from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from time import monotonic

from app.chat import generate_reply
from app.model_calls import model_call_events
from app.orchestration.classifier import TurnIntent, classify_turn
from app.orchestration.planner import plan_searches
from app.orchestration.contracts import ResearchContract
from app.research import SearchUnavailable, diverse_sources, extract_pages, live_source, search, source_domain, urls_in_message
from app.schemas import ChatTurn


@dataclass
class TurnResult:
    reply: str
    sources: list[dict[str, str]]
    debug: dict


def run_turn(message: str, history: list[ChatTurn], *, search_requested: bool, seed_sources: list[dict] | None = None, contract: ResearchContract | None = None, verification_cache: dict | None = None) -> TurnResult:
    started = monotonic()
    intent = classify_turn(search_requested=search_requested)
    sources: list[dict[str, str]] = []
    plan_debug = None
    if intent == TurnIntent.SEARCH:
        planning_request = "Original request:\n" + message
        if history:
            planning_request += "\nRecent context; target unresolved requirements, do not repeat completed work:\n" + "\n".join(turn.content for turn in history[-2:])[-16000:]
        plan, plan_debug = plan_searches(planning_request, contract)
        contract = plan.contract
        urls = urls_in_message(message)
        if urls:
            sources = extract_pages(urls, fresh=contract.shopping, focus=message)
        else:
            collected: dict[str, dict[str, str]] = {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(search, query.query, objective=query.objective, country=contract.country_code) for query in plan.queries]
                batches = []
                for future in futures:
                    try:
                        batches.append(future.result())
                    except SearchUnavailable:
                        continue
                for index in range(10):
                    for batch in batches:
                        if index < len(batch):
                            source = batch[index]
                            collected.setdefault(source["url"], source)
            if not collected:
                raise SearchUnavailable("Exa did not return readable search results for this request.")
            sources = diverse_sources(list(collected.values()), 24 if contract.shopping else 30)
            cached = {source["url"]: source for source in (seed_sources or [])
                      if (live_source(source) if contract.shopping else source.get("retrieval") == "page")}
            pending = [source for source in sources if source["url"] not in cached]
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(extract_pages, [s["url"] for s in pending[start:start + 8]], limit=8,
                                           fresh=contract.shopping, focus=message)
                               for start in range(0, len(pending), 8)]
                    pages = []
                    for future in futures:
                        try:
                            pages.extend(future.result())
                        except SearchUnavailable:
                            continue
                page_by_url = {**cached, **{page["url"]: page for page in pages}}
                sources = [page_by_url.get(source["url"], source) for source in sources]
            except SearchUnavailable:
                pass
    if seed_sources:
        combined = {source["url"]: source for source in sources}
        for source in seed_sources:
            combined.setdefault(source["url"], source)
        sources = diverse_sources(list(combined.values()), 30)
    retrieval_seconds = round(monotonic() - started, 2)
    retrieval = {"sources": len(sources), "domains": len({source_domain(s["url"]) for s in sources}),
                 "live_pages": sum(live_source(s) for s in sources), "elapsed_seconds": retrieval_seconds}
    observer = model_call_events.get()
    if observer and search_requested:
        observer({"stage": "retrieval", "status": "completed", "search_plan": plan_debug,
                  "retrieval": retrieval, "sources": [{k: v for k, v in source.items() if k != "text"} for source in sources]})
    reply, debug = generate_reply(message, history, sources, contract=contract, verification_cache=verification_cache)
    debug["intent"] = intent.value
    debug["search_plan"] = plan_debug
    debug["research_contract"] = contract.model_dump() if contract else None
    debug["retrieval"] = retrieval
    return TurnResult(reply=reply, sources=sources, debug=debug)
