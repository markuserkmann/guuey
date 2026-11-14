from copy import deepcopy

from evaluate_pipeline import acceptance_metrics, configured_models, diagnostics, final_structured, observed_models


def record(case, findings, requirements, sources, *, rows=0, budget=None):
    reply = "| Destination | Hotel |\n| --- | --- |\n" + "".join(f"| Place {i} | Stay {i} |\n" for i in range(rows)) if rows else "Answer"
    return {"case_id": case, "run": {"updated_at": "2026-11-14T12:00:00+00:00", "result": {"reply": reply, "sources": sources}},
            "debug": {"task_state": {"answer": {"findings": findings}, "contract": {"requirements": requirements, "budget": budget}}}}


def check(identifier, url, quote, **extra):
    return {"requirement_id": identifier, "status": "met", "statement": quote,
            "evidence": [{"source_url": url, "quote": quote}], **extra}


def hotels():
    findings, sources = [], []
    for i in range(5):
        url = f"https://hotel{i}.example/"
        quote = f"Hotel {i} is located in Town {i}. Town {i} has free public parks."
        sources.append({"url": url, "title": f"Hotel {i}", "text": quote})
        findings.append({"name": f"Town {i}", "source_url": url,
                         "checks": [check("hotel", url, quote, scope="related", related_entity=f"Hotel {i}"),
                                    check("budget", url, quote, strength="preference")]})
    return record("estonia_hotels", findings, [{"id": "hotel", "description": "Hotel for destination", "scope": "related"},
                  {"id": "budget", "description": "Budget-friendly destination", "strength": "preference"}], sources, rows=5)


def test_five_named_accommodation_leads_do_not_pass_hotel_checks():
    data = hotels()
    for finding in data["debug"]["task_state"]["answer"]["findings"]:
        finding["checks"][0]["status"] = "unknown"
    result = acceptance_metrics(data)
    assert result["named_stay_destination_count"] == 5
    assert result["fully_supported_related_destination_count"] == 0
    assert not result["task_acceptance_passed"]


def test_five_supported_related_stays_and_five_table_rows_pass_structural_gate():
    result = acceptance_metrics(hotels())
    assert result["fully_supported_related_check_count"] == 5
    assert result["task_acceptance_passed"]


def test_related_stay_requires_real_quote_and_destination_relationship():
    data = hotels()
    data["run"]["result"]["sources"][0]["text"] = "Hotel 0 exists."
    data["debug"]["task_state"]["answer"]["findings"][0]["checks"][0]["evidence"][0]["quote"] = "Hotel 0 exists."
    result = acceptance_metrics(data)
    assert result["named_stay_destination_count"] == 5
    assert result["fully_supported_related_destination_count"] == 4
    assert not result["task_acceptance_passed"]
    data["debug"]["task_state"]["answer"]["findings"][1]["checks"][0]["evidence"][0]["quote"] = "Invented quote"
    assert acceptance_metrics(data)["named_stay_destination_count"] == 4


def test_hotel_and_hotell_prefix_aliases_match_named_property():
    data = hotels()
    for index, entity in enumerate(("Hotel Legend", "Hotell Starest")):
        source = data["run"]["result"]["sources"][index]
        core = entity.split(" ", 1)[1]
        source["title"] = core
        source["text"] = f"{core} is located in Town {index}. Town {index} has free public parks."
        item = data["debug"]["task_state"]["answer"]["findings"][index]["checks"][0]
        item["related_entity"] = entity
        item["evidence"][0]["quote"] = source["text"]
        data["debug"]["task_state"]["answer"]["findings"][index]["checks"][1]["evidence"][0]["quote"] = source["text"]
    result = acceptance_metrics(data)
    assert result["named_stay_destination_count"] == 5
    assert result["fully_supported_related_destination_count"] == 5
    assert result["task_acceptance_passed"]


def test_explicit_hostel_is_not_accepted_as_requested_hotel():
    data = hotels()
    data["debug"]["task_state"]["contract"]["requirements"][0]["description"] = "Named hotel for this destination, with its location stated"
    source = data["run"]["result"]["sources"][0]
    source["title"] = "Munkenhof Hostel"
    source["text"] = "Munkenhof Hostel is located in Town 0."
    item = data["debug"]["task_state"]["answer"]["findings"][0]["checks"][0]
    item["related_entity"] = "Munkenhof Hostel"
    item["evidence"][0]["quote"] = source["text"]
    result = acceptance_metrics(data)
    assert result["named_stay_destination_count"] == 5
    assert result["fully_supported_related_destination_count"] == 4
    assert not result["task_acceptance_passed"]


def test_supported_budget_preference_cannot_replace_unknown_required_hotel():
    data = hotels()
    data["debug"]["task_state"]["contract"]["requirements"].append({"id": "hotel_value", "description": "Budget hotel positioning", "scope": "related", "strength": "preference"})
    item = data["debug"]["task_state"]["answer"]["findings"][0]["checks"][0]
    preference = deepcopy(item)
    preference["requirement_id"] = "hotel_value"
    item["status"] = "unknown"
    data["debug"]["task_state"]["answer"]["findings"][0]["checks"].append(preference)
    assert acceptance_metrics(data)["fully_supported_related_destination_count"] == 4
    assert not acceptance_metrics(data)["task_acceptance_passed"]


def test_five_hotels_without_budget_evidence_do_not_satisfy_budget_trip():
    data = hotels()
    for finding in data["debug"]["task_state"]["answer"]["findings"]:
        finding["checks"][1]["status"] = "unknown"
    result = acceptance_metrics(data)
    assert result["fully_supported_related_destination_count"] == 5
    assert result["budget_supported_destination_count"] == 0
    assert not result["task_acceptance_passed"]


def test_hard_budget_requirement_counts_when_it_has_meaningful_source_evidence():
    data = hotels()
    data["debug"]["task_state"]["contract"]["requirements"][1]["strength"] = "hard"
    result = acceptance_metrics(data)
    assert result["budget_supported_destination_count"] == 5
    assert result["task_acceptance_passed"]


def test_midrange_and_undefined_budget_symbols_are_not_affordability_evidence():
    data = hotels()
    for i, quote in enumerate(("Town 0 has mid-range budget positioning.", "Town 1 | Budget | 💰")):
        source = data["run"]["result"]["sources"][i]
        source["text"] += "\n\n" + quote
        item = data["debug"]["task_state"]["answer"]["findings"][i]["checks"][1]
        item["statement"] = "A budget-friendly destination."
        item["evidence"][0]["quote"] = quote
    result = acceptance_metrics(data)
    assert result["budget_supported_destination_count"] == 3
    assert not result["task_acceptance_passed"]


def test_stale_hotel_rate_does_not_count_as_current_budget_evidence():
    data = hotels()
    item = data["debug"]["task_state"]["answer"]["findings"][0]["checks"][1]
    source = data["run"]["result"]["sources"][0]
    source["text"] += " Room rate EUR34."
    item["statement"] = "Room rate EUR34."
    item["evidence"][0]["quote"] = "Room rate EUR34."
    result = acceptance_metrics(data)
    assert result["budget_supported_destination_count"] == 4
    assert not result["task_acceptance_passed"]


def test_final_task_state_wins_over_prior_stage_success():
    data = hotels()
    data["debug"]["stages"] = [{"output": deepcopy(data["debug"]["task_state"]["answer"])}]
    data["debug"]["task_state"]["answer"]["findings"] = []
    assert final_structured(data)[2] == "task_state.answer"
    assert not acceptance_metrics(data)["task_acceptance_passed"]


def laptops():
    requirements = [{"id": key, "kind": key, "description": key} for key in ("price", "stock", "delivery")]
    requirements += [{"id": "ram", "description": "16 GB RAM"}, {"id": "ssd", "description": "512 GB SSD"}]
    sources, findings = [], []
    for i in range(3):
        url = f"https://seller{i}.example/product/laptop"
        text = f"Laptop {i}: EUR 699. In stock. Ships to Finland. 16 GB RAM. 512 GB SSD."
        sources.append({"url": url, "title": f"Laptop {i}", "text": text, "retrieval": "live", "retrieved_at": "2026-11-14T11:59:00+00:00"})
        findings.append({"name": f"Laptop {i}", "source_url": url, "source_kind": "product",
                         "checks": [check(r["id"], url, text) for r in requirements]})
    return record("finland_laptops", findings, requirements, sources, budget={"amount": 700, "currency": "EUR", "operator": "lt"})


def test_current_offer_gate_uses_recorded_evaluation_time_and_all_three_requirements():
    data = laptops()
    result = acceptance_metrics(data)
    assert result["fully_verified_current_offer_count"] == 3
    assert result["task_acceptance_passed"]
    data["debug"]["task_state"]["answer"]["findings"][0]["checks"][2]["status"] = "unknown"
    result = acceptance_metrics(data)
    assert result["known_requirement_counts"]["delivery"] == 2
    assert result["fully_verified_current_offer_count"] == 2
    assert not result["task_acceptance_passed"]


def test_stale_prices_or_relaxed_budget_do_not_pass():
    data = laptops()
    data["run"]["result"]["sources"][0]["retrieved_at"] = "2026-11-14T11:40:00+00:00"
    result = acceptance_metrics(data)
    assert result["known_requirement_counts"]["price"] == 3
    assert result["known_requirement_counts"]["price_current"] == 2
    assert not result["task_acceptance_passed"]
    data = laptops()
    data["debug"]["task_state"]["contract"]["budget"]["operator"] = "lte"
    assert not acceptance_metrics(data)["task_acceptance_passed"]


def test_duplicate_offer_does_not_count_as_three_verified_offers():
    data = laptops()
    findings = data["debug"]["task_state"]["answer"]["findings"]
    findings[2] = deepcopy(findings[1])
    result = acceptance_metrics(data)
    assert result["fully_verified_current_offer_count"] == 2
    assert not result["task_acceptance_passed"]


def test_named_related_restaurants_cannot_pass_hotel_requirement():
    data = hotels()
    data["debug"]["task_state"]["contract"]["requirements"] = [{"id": "dining", "description": "Restaurant for destination", "scope": "related"}]
    for finding in data["debug"]["task_state"]["answer"]["findings"]:
        finding["checks"][0]["requirement_id"] = "dining"
    assert acceptance_metrics(data)["fully_supported_related_destination_count"] == 0
    assert not acceptance_metrics(data)["task_acceptance_passed"]


def test_sauna_needs_booking_and_all_requested_information():
    url = "https://sauna.example/public"
    quote = "Adult price EUR29; duration two hours; includes towel; booking online."
    requirements = [{"id": name, "kind": "price" if name == "price" else "fact", "description": name}
                    for name in ("price", "duration", "inclusions", "booking")]
    data = record("loyly_price", [{"name": "Loyly", "checks": [check(r["id"], url, quote) for r in requirements]}], requirements,
                  [{"url": url, "text": quote, "retrieval": "live", "retrieved_at": "2026-11-14T11:59:00+00:00"}])
    assert acceptance_metrics(data)["task_acceptance_passed"]
    data["debug"]["task_state"]["answer"]["findings"][0]["checks"].pop()
    assert not acceptance_metrics(data)["task_acceptance_passed"]


def test_models_record_actual_stage_debug_separately_from_local_configuration(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "base-test")
    monkeypatch.setenv("GEMINI_RESEARCH_MODEL", "research-test")
    assert configured_models()["research"] == "research-test"
    actual = observed_models({"stages": [{"model": "server-research", "verification_model": "server-verifier", "search_plan": {"model": "server-planner"}}]})
    assert actual == [{"round": 1, "research": "server-research", "verification": "server-verifier", "planning": "server-planner"}]


def test_cost_unavailable_is_distinct_from_explicit_zero():
    absent = diagnostics({"routing_usage": {"input_tokens": 50}, "stages": [{"usage": {"cost": None, "input_tokens": 100}}]})
    assert absent["provider_usage_reported_cost_usd"] is None
    assert absent["provider_cost_report_count"] == 0
    assert absent["provider_usage_report_count"] == 2
    assert not absent["provider_cost_reporting_complete"]
    zero = diagnostics({"routing_usage": {"cost": "0", "input_tokens": 50}})
    assert zero["provider_usage_reported_cost_usd"] == 0
    assert zero["provider_cost_report_count"] == 1
    assert zero["provider_cost_reporting_complete"]


def test_partial_cost_reports_are_labeled_subtotal_and_invalid_costs_ignored():
    result = diagnostics({"routing_usage": {"cost": "0.01"}, "stages": [
        {"usage": {"input_tokens": 100}, "search_plan": {"usage": {"cost": "0.02"}}},
        {"usage": {"cost": "NaN"}, "search_plan": {"usage": {"cost": -1}}},
    ]})
    assert result["provider_usage_reported_cost_usd"] == 0.03
    assert result["provider_cost_report_count"] == 2
    assert result["provider_usage_report_count"] == 5
    assert not result["provider_cost_reporting_complete"]
    assert "subtotal" in result["cost_scope"]


def test_free_wifi_alone_does_not_establish_budget_suitability():
    data = hotels()
    source = data["run"]["result"]["sources"][0]
    quote = "Hotel 0 in Town 0 offers free WiFi in public areas."
    source["text"] += " " + quote
    item = data["debug"]["task_state"]["answer"]["findings"][0]["checks"][1]
    item["statement"] = quote
    item["evidence"][0]["quote"] = quote
    assert acceptance_metrics(data)["budget_supported_destination_count"] == 4


def test_primary_budget_claim_cannot_substitute_another_hotel():
    data = hotels()
    source = data["run"]["result"]["sources"][0]
    quote = "Other Hotel in Town 0 offers cheap rooms."
    source["text"] += " " + quote
    item = data["debug"]["task_state"]["answer"]["findings"][0]["checks"][1]
    item["statement"] = quote
    item["evidence"][0]["quote"] = quote
    metrics = acceptance_metrics(data)
    assert metrics["budget_supported_destination_count"] == 4
    assert "another hotel" in metrics["candidates"][0]["budget_evidence_rejections"]["budget"]


def test_round_metrics_count_retries_and_duplicate_crawls():
    events = []
    for index in (1, 2):
        events.extend([
            {"round": index, "stage": "planning", "status": "completed", "elapsed_seconds": 2},
            {"round": index, "stage": "answering", "status": "completed", "elapsed_seconds": 5},
            {"round": index, "stage": "verifying", "status": "completed", "elapsed_seconds": 1},
            {"round": index, "stage": "retrieval_request", "endpoint": "contents", "status": "started",
             "livecrawl": True, "urls": ["https://seller.example/a"]},
        ])
    stage = {"generation_usage": {"input_tokens": 1234, "requests": 2}, "retrieval": {"elapsed_seconds": 3},
             "coverage": {"supported_checks": 4, "verified": 1, "retained": 2}}
    rounds = diagnostics({"stages": [stage, stage], "model_events": events})["rounds"]
    assert rounds[0]["duplicate_crawled_urls"] == 0
    assert rounds[1]["duplicate_crawled_urls"] == 1
    assert {key: rounds[0][key] for key in ("planning_s", "retrieval_s", "answering_s", "verifying_s")} == {
        "planning_s": 2, "retrieval_s": 3, "answering_s": 5, "verifying_s": 1}
    assert rounds[0]["generation_requests"] == 2
    assert rounds[0]["contents_calls"] == 1
    assert rounds[0]["generation_input_tokens"] == 1234


def test_parallel_retrieval_metrics_use_wall_time_and_exclude_planning():
    events = [
        {'round': 1, 'stage': 'planning', 'status': 'completed', 'elapsed_seconds': 8},
        {'round': 1, 'stage': 'retrieval_request', 'status': 'completed', 'elapsed_seconds': 4, 'run_elapsed_s': 14},
        {'round': 1, 'stage': 'retrieval_request', 'status': 'completed', 'elapsed_seconds': 3, 'run_elapsed_s': 14},
        {'round': 1, 'stage': 'retrieval_request', 'status': 'completed', 'elapsed_seconds': 2, 'run_elapsed_s': 17},
    ]
    result = diagnostics({'stages': [{'retrieval': {'elapsed_seconds': 17}}], 'model_events': events})
    assert result['rounds'][0]['retrieval_s'] == 6
    assert result['rounds'][0]['planning_s'] == 8
