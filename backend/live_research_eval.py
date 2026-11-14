"""Opt-in live research evaluation; uses configured Exa/Gemini and writes no chats."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from time import monotonic


CASES = [
    ("laptop_budget", "Find 3 laptops I can buy in Finland for under €700, with at least 16 GB RAM and a 512 GB SSD. Show exact configuration, current price, stock and seller links."),
    ("laptop_linux", "Find 3 laptops sold in Finland around €1000 with at least 16 GB RAM and documented Linux compatibility. Distinguish confirmed compatibility from assumptions and show current prices."),
    ("laptop_macbook", "Compare 2 current MacBook Air 13-inch offers with 16 GB RAM sold in Finland. Give exact model, storage, total advertised price and availability; do not mix configurations."),
    ("laptop_refurbished", "Find 3 refurbished ThinkPad laptops available to buy in Finland under €500 with 16 GB RAM. Show condition, warranty, keyboard layout and current prices, marking missing details."),
    ("laptop_gaming", "Find 2 gaming laptops sold in Finland under €1200 with an RTX 4060 or better and 16 GB RAM. Show exact GPU, price, stock and Finnish purchase or delivery evidence."),
    ("sauna_public", "Compare 3 public saunas in Helsinki with adult admission prices and session lengths. Include an official booking link and distinguish per-person admission from private hire."),
    ("sauna_loyly", "What does an adult public sauna session at Löyly Helsinki cost, how long is it, and what is included? Use the operator's current information and give the booking link."),
    ("sauna_private", "Find 3 private saunas in Helsinki for a group of 8. Compare advertised hire prices, duration and capacity. Do not treat per-person public admission as a private hire price."),
    ("sauna_floating", "Find floating saunas near Hotel Matts in Espoo that can be hired. Show hire prices and evidence of location relative to the hotel. Do not substitute shoreline saunas for floating ones."),
    ("sauna_slot", "Can 2 adults book a public sauna at Löyly Helsinki on 21 November 2026 at 18:00? Show the published price and booking link; clearly state whether that exact slot is actually verified available."),
    ("holiday_winter", "Compare 3 warm European holiday destinations for a one-week January trip from Helsinki. Explain likely weather, flight practicality and main tradeoffs with sources. Do not invent live flight fares."),
    ("holiday_family", "Suggest 3 Finnish holiday destinations for a family with two school-age children in February. Compare activities, transport from Helsinki and practical downsides using official destination information."),
    ("holiday_lapland", "Compare Levi and Ruka for a beginner ski holiday in February 2027. Give published adult lift-pass prices if available, mark the season and pass duration, and compare access from Helsinki."),
    ("holiday_ferry", "Compare the Helsinki to Tallinn ferry operators for a day trip. Give typical crossing duration, departure terminals and where to check prices. Do not present a starting fare as a guaranteed return fare."),
    ("holiday_suomenlinna", "Plan a day trip from central Helsinki to Suomenlinna in November. Explain the public ferry ticket zones, museum opening limitations and useful official links with current published prices where available."),
]


def load_environment():
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name, value = name.strip(), value.strip()
            if name in {"GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "EXA_API_KEY"}:
                os.environ.setdefault(name, value.strip("\"'"))
    if not os.getenv("EXA_API_KEY") or not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        raise SystemExit("Live evaluation requires EXA_API_KEY and a Gemini API key.")


def run_case(case, output, followup_from=None):
    from app.model_calls import error_details, model_call_events
    from app.orchestration.contracts import ResearchContract
    from app.orchestration.orchestrator import run_turn
    from app.schemas import ChatTurn

    case_id, prompt = case
    started = monotonic()
    events = []
    def observe(event):
        timed = {**event, "total_elapsed_seconds": round(monotonic() - started, 2)}
        events.append(timed)
        with (output / f"{case_id}.events.jsonl").open("a") as stream:
            stream.write(json.dumps(timed, ensure_ascii=False, default=str) + "\n")
    token = model_call_events.set(observe)
    record = {"id": case_id, "prompt": prompt, "started_at": datetime.now(UTC).isoformat(), "scope": "one complete run_turn research round"}
    try:
        history, seeds, contract = [], None, None
        if followup_from:
            previous = json.loads((followup_from / f"{case_id}.json").read_text())
            seeds = previous["sources"]
            contract = ResearchContract.model_validate(previous["debug"]["research_contract"])
            history = [ChatTurn(role="assistant", content=previous["answer"]), ChatTurn(role="user", content=
                "Continue researching the original request. Resolve missing evidence and find additional matching items. "
                "Use candidate names and target these per-candidate gaps: "
                + json.dumps(previous["debug"].get("coverage", {}).get("gaps", []), ensure_ascii=False)[:8000])]
            record["scope"] = "one follow-up research round seeded from " + str(followup_from)
        result = run_turn(prompt, history, search_requested=True, seed_sources=seeds, contract=contract)
        cited = set(re.findall(r"\]\(<?(https?://[^)>]+)>?\)", result.reply))
        supplied = {source["url"] for source in result.sources}
        record.update({"status": "completed", "answer": result.reply, "sources": result.sources, "debug": result.debug,
                       "mechanical_checks": {"nonempty_answer": bool(result.reply.strip()), "source_count": len(result.sources),
                                             "citation_count": len(cited), "unsupplied_citations": sorted(cited - supplied),
                                             "validation_issues": result.debug.get("validation_issues", []),
                                             "coverage": result.debug.get("coverage", {}),
                                             "has_gaps": result.debug.get("has_gaps")},
                       "quality_review": {"status": "requires_review", "rubric": [
                           "Every price has correct item/configuration, currency, unit, duration and qualifying conditions.",
                           "Availability claims distinguish current stock/bookable service from verified date-specific slots.",
                           "All user constraints are addressed; unsupported details are explicitly unknown.",
                           "Cited evidence supports claims and uses primary operator/retailer sources where possible.",
                           "Answer gives useful options without padding or inappropriate product/service requirements."]}})
    except Exception as exc:
        record.update({"status": "error", "error": error_details(exc)})
    finally:
        model_call_events.reset(token)
    record["elapsed_seconds"] = round(monotonic() - started, 2)
    record["events"] = events
    (output / f"{case_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"id": case_id, "status": record["status"], "elapsed_seconds": record["elapsed_seconds"],
                      "checks": record.get("mechanical_checks"), "error": record.get("error", {}).get("message")}), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--followup-from", type=Path)
    args = parser.parse_args()
    load_environment()
    cases = [case for case in CASES if not args.cases or case[0] in args.cases]
    if args.cases and set(args.cases) - {case[0] for case in cases}:
        parser.error("Unknown case IDs")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "run-config.json").write_text(json.dumps({
        "started_at": datetime.now(UTC).isoformat(), "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "concurrency": args.concurrency, "case_ids": [case[0] for case in cases],
        "source_hashes": {str(path.relative_to(Path(__file__).parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (Path(__file__).parent / "app").rglob("*.py")},
    }, indent=2))
    started = monotonic()
    records = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_case, case, args.output, args.followup_from) for case in cases]
        for future in as_completed(futures):
            records.append(future.result())
    summary = {"completed_at": datetime.now(UTC).isoformat(), "wall_seconds": round(monotonic() - started, 2),
               "concurrency": args.concurrency, "cases": [{key: record.get(key) for key in
                   ("id", "status", "elapsed_seconds", "mechanical_checks", "error")} for record in records],
               "note": "Mechanical checks are diagnostics, not an independent factual accuracy score. Full evidence is stored per case."}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
