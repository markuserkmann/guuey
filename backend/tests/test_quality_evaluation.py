from copy import deepcopy
from types import SimpleNamespace

from evaluate_pipeline import CASES, quota_error, run_protocol
from evaluations.score_quality import compare_scores, fingerprint, score_record


def capture():
    url = "https://seller.example/product/laptop"
    return {"case_id": "finland_laptops", "status": "partial", "run_id": "test-run",
            "run": {"result": {"reply": "Laptop: €559. [Source](https://seller.example/product/laptop)",
                               "sources": [{"url": url, "title": "Laptop", "text": "Laptop: €559. 16 GB RAM."}]}},
            "debug": {"task_state": {"contract": {"shopping": True, "activity": "product"},
                      "answer": {"findings": [{"name": "Laptop", "source_url": url, "checks": [{
                          "requirement_id": "price", "requirement": "Price under €700", "status": "met", "basis": "confirmed",
                          "statement": "€559", "evidence": [{"source_url": url, "quote": "Laptop: €559."}]}]}]}}}}


def gold():
    return {"audit_status": "audited captured fact", "facts": [
        {"id": "laptop_price", "candidate": "Laptop", "requirement_pattern": "price", "expected_value": "€559",
         "acceptable_source_classes": ["retailer"], "quote": "Laptop: €559."}]}


def check(record):
    return record["debug"]["task_state"]["answer"]["findings"][0]["checks"][0]


def test_gold_recall_is_separate_from_unknown_semantic_precision():
    result = score_record(capture(), gold())
    assert result["recall"] == 1
    assert result["precision"]["confirmed"]["shown"] == 1
    assert result["precision"]["confirmed"]["precision"] is None
    assert result["precision"]["confirmed"]["audit_coverage"] == 0


def test_unsupplied_or_changed_quotes_fail_even_when_the_verifier_says_met():
    data = capture()
    check(data)["evidence"][0]["quote"] = "The laptop is €499."
    result = score_record(data, gold())
    assert result["labels"] == {"unsupported": 1}
    assert result["recall"] == 0
    assert result["misses"][0]["kind"] == "pipeline_miss"
    data["run"]["result"]["reply"] += " [Buy](https://invented.example/)"
    assert score_record(data)["unsupplied_citations"] == ["https://invented.example/"]


def test_gold_value_only_in_quote_is_a_pipeline_miss_if_omitted_from_display():
    data = capture()
    check(data)["statement"] = "The laptop has RAM."
    result = score_record(data, gold())
    assert result["recall"] == 0
    assert result["misses"][0]["kind"] == "pipeline_miss"


def test_gold_rejects_a_different_entity_and_disallowed_source_class():
    data = capture()
    data["debug"]["task_state"]["answer"]["findings"][0]["name"] = "Different laptop"
    assert score_record(data, gold())["recall"] == 0
    data = capture()
    result = score_record(data, gold(), {"source_classes": {"https://seller.example/product/laptop": "seo"}})
    assert result["recall"] == 0
    assert result["seo_or_off_target_only_facts"] == 1


def test_audits_are_tied_to_exact_claim_and_quote_fingerprints():
    data = capture()
    first = score_record(data)["facts"][0]
    audits = {"claims": {fingerprint(first): {"label": "correct", "reason": "Audited against captured passage."}}}
    assert score_record(data, audits=audits)["precision"]["confirmed"]["precision"] == 1
    check(data)["statement"] += " Includes free delivery."
    assert score_record(data, audits=audits)["precision"]["confirmed"]["precision"] is None


def test_empty_gold_does_not_pass_recall_or_regression_gate():
    before = score_record(capture(), gold())
    after = score_record(capture())
    assert after["recall"] is None
    comparison = compare_scores([before], [after])[0]
    assert not comparison["precision_not_lower"]
    assert not comparison["recall_within_five_percentage_points"]


def test_retrieval_miss_is_not_a_pipeline_miss():
    data = capture()
    data["run"]["result"]["sources"][0]["text"] = "Laptop information only."
    result = score_record(data, gold())
    assert result["misses"][0]["kind"] == "retrieval_miss"
    assert result["pipeline_miss_rate"] == 0


def test_protocol_includes_all_ten_cases_and_stops_after_quota(monkeypatch, tmp_path):
    assert len(CASES) == 10
    calls = []

    def run(case, args, token, secrets, **options):
        calls.append(case)
        return {"case_id": case, "status": "failed", "error": {"code": "quota_exceeded"}}

    monkeypatch.setattr("evaluate_pipeline.run_case", run)
    args = SimpleNamespace(cases=list(CASES), followup=True, cache_checks=True, repetitions=2, concurrency=2, output=tmp_path)
    result = run_protocol(args, "not-a-real-token", [])
    assert len(result) == len(calls) == 2
    assert quota_error(result[0])


def test_protocol_preserves_same_chat_and_records_both_laptop_runs(monkeypatch, tmp_path):
    calls = []

    def run(case, args, token, secrets, **options):
        calls.append((case, options))
        return {"case_id": case, "chat_id": "laptop-chat", "run_id": case, "status": "partial"}

    monkeypatch.setattr("evaluate_pipeline.run_case", run)
    args = SimpleNamespace(cases=["followup_laptops"], followup=False, cache_checks=False, repetitions=1, concurrency=2, output=tmp_path)
    result = run_protocol(args, "not-a-real-token", [])
    assert [r["case_id"] for r in result] == ["finland_laptops", "followup_laptops"]
    assert calls[1][1] == {"chat_id": "laptop-chat", "predecessor": "finland_laptops"}


def test_knowledge_points_are_audited_independently():
    data = capture()
    data["debug"]["task_state"]["answer"] = {"knowledge": {"points": [
        {"statement": "Price €559", "basis": "reported", "evidence": deepcopy(check(capture())["evidence"])},
        {"statement": "Invented rule", "evidence": [{"source_url": "https://invented.example/", "quote": "Invented rule"}]},
    ]}}
    result = score_record(data)
    assert len(result["facts"]) == 2
    assert result["facts"][0]["basis"] == "reported"
    assert result["facts"][1]["label"] == "unsupported"
