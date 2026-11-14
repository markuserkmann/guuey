from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.chat import generate_reply
from app.orchestration.classifier import TurnIntent, classify_turn
from app.orchestration.planner import plan_searches
from app.research import SearchUnavailable, extract_pages, search, urls_in_message
from app.schemas import ChatTurn


@dataclass
class TurnResult:
    reply: str
    sources: list[dict[str, str]]
    debug: dict


def run_turn(message: str, history: list[ChatTurn], *, search_requested: bool) -> TurnResult:
    intent = classify_turn(search_requested=search_requested)
    sources: list[dict[str, str]] = []
    plan_debug = None
    if intent == TurnIntent.SEARCH:
        urls = urls_in_message(message)
        if urls:
            sources = extract_pages(urls)
        else:
            queries, plan_debug = plan_searches(message)
            collected: dict[str, dict[str, str]] = {}
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(search, query) for query in queries]
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
            sources = list(collected.values())[:10]
            try:
                pages = extract_pages([source["url"] for source in sources], limit=10)
                page_by_url = {page["url"]: page for page in pages}
                sources = [page_by_url.get(source["url"], source) for source in sources]
            except SearchUnavailable:
                pass
    reply, debug = generate_reply(message, history, sources)
    debug["intent"] = intent.value
    debug["search_plan"] = plan_debug
    return TurnResult(reply=reply, sources=sources, debug=debug)
