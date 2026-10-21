from app.evaluation.run_full_evaluation import retrieval_metrics
from app.evaluation.scenario_corpus import scenarios


def test_phase_eleven_corpus_has_required_distribution() -> None:
    rows = scenarios()

    assert len(rows) >= 100
    assert sum(row["expected_intent"] == "dns_issue" for row in rows) >= 20
    assert sum(row["expected_intent"] == "ssl_issue" for row in rows) >= 15
    assert sum(row["expected_intent"] == "website_down" for row in rows) >= 20
    assert all("scenario_id" in row and "expected_tools" in row for row in rows)


def test_dense_retrieval_comparison_reports_rank_metrics() -> None:
    metrics, failures = retrieval_metrics(scenarios(), 320)

    assert {"precision_at_1", "precision_at_3", "recall_at_3", "mrr"} <= set(metrics)
    assert failures
