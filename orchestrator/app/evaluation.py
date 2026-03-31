from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from statistics import mean
from typing import Any

from app.config import settings

IDK_ANSWER = "i don't know."


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]{3,}", text.lower()))


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _is_idk(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {
        IDK_ANSWER,
        "i dont know.",
        "i don't know",
        "i dont know",
    }


def _f1_token_overlap(predicted: str, expected: str) -> float:
    pred_tokens = _tokenize(predicted)
    exp_tokens = _tokenize(expected)
    if not pred_tokens or not exp_tokens:
        return 0.0

    overlap = len(pred_tokens.intersection(exp_tokens))
    if overlap == 0:
        return 0.0

    precision = overlap / len(pred_tokens)
    recall = overlap / len(exp_tokens)
    return (2 * precision * recall) / (precision + recall)


def _score_faithfulness(answer: str, context: str, expected_behavior: str) -> float:
    if expected_behavior == "refuse":
        return 1.0 if _is_idk(answer) else 0.0

    answer_tokens = _tokenize(answer)
    if not answer_tokens:
        return 0.0

    context_tokens = _tokenize(context)
    if not context_tokens:
        return 0.0

    supported = len(answer_tokens.intersection(context_tokens))
    return supported / len(answer_tokens)


def _score_relevance(question: str, answer: str, context: str) -> float:
    question_tokens = _tokenize(question)
    answer_tokens = _tokenize(answer)
    context_tokens = _tokenize(context)

    if not answer_tokens:
        return 0.0

    qa_overlap = len(answer_tokens.intersection(question_tokens)) / max(len(question_tokens), 1)
    ac_overlap = len(answer_tokens.intersection(context_tokens)) / max(len(answer_tokens), 1)
    return min(1.0, 0.5 * qa_overlap + 0.5 * ac_overlap)


def _score_correctness(answer: str, expected_answer: str, expected_behavior: str, context: str) -> float:
    if expected_behavior == "refuse":
        return 1.0 if _is_idk(answer) else 0.0

    if _is_idk(answer):
        return 0.0

    # Use expected answer overlap when provided, fallback to context grounding.
    if expected_answer.strip():
        return _f1_token_overlap(answer, expected_answer)

    answer_tokens = _tokenize(answer)
    context_tokens = _tokenize(context)
    if not answer_tokens or not context_tokens:
        return 0.0
    return len(answer_tokens.intersection(context_tokens)) / len(answer_tokens)


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _aggregate_case_metrics(case_reports: list[dict[str, Any]]) -> dict[str, float]:
    faithfulness_scores = [float(case["metrics"]["faithfulness"]) for case in case_reports]
    relevance_scores = [float(case["metrics"]["relevance"]) for case in case_reports]
    correctness_scores = [float(case["metrics"]["correctness"]) for case in case_reports]

    faithfulness_avg = _safe_mean(faithfulness_scores)
    relevance_avg = _safe_mean(relevance_scores)
    correctness_avg = _safe_mean(correctness_scores)

    return {
        "faithfulness": round(faithfulness_avg, 4),
        "relevance": round(relevance_avg, 4),
        "correctness": round(correctness_avg, 4),
        "overall": round((faithfulness_avg + relevance_avg + correctness_avg) / 3, 4),
    }


def run_benchmark(
    pipeline: Any,
    benchmark_path: str = "/app/data/eval_benchmark.json",
    model_version_label: str | None = None,
) -> dict[str, Any]:
    with open(benchmark_path, encoding="utf-8") as benchmark_file:
        cases = json.load(benchmark_file)

    case_reports: list[dict[str, Any]] = []

    for index, case in enumerate(cases):
        question = str(case["question"])
        expected_answer = str(case.get("expected_answer", ""))
        expected_behavior = str(case.get("expected_behavior", "answer"))
        session_id = f"eval-{index}-{case.get('id', 'case')}"

        result = pipeline.ask(question, session_id=session_id)
        answer = str(result.get("answer", ""))

        rewritten_query = pipeline._rewrite_query(question)  # pylint: disable=protected-access
        intent = pipeline._classify_intent(question)  # pylint: disable=protected-access
        ranked_hits = pipeline._hybrid_retrieve(intent, rewritten_query)  # pylint: disable=protected-access
        context = "\n\n".join(hit["doc"].page_content for hit in ranked_hits)

        faithfulness = _score_faithfulness(answer, context, expected_behavior)
        relevance = _score_relevance(question, answer, context)
        correctness = _score_correctness(answer, expected_answer, expected_behavior, context)

        case_reports.append(
            {
                "id": case.get("id", f"case-{index}"),
                "category": case.get("category", "unknown"),
                "question": question,
                "expected_answer": expected_answer,
                "expected_behavior": expected_behavior,
                "prediction": answer,
                "metrics": {
                    "faithfulness": round(faithfulness, 4),
                    "relevance": round(relevance, 4),
                    "correctness": round(correctness, 4),
                    "overall": round((faithfulness + relevance + correctness) / 3, 4),
                },
                "retrieval_status": result.get("retrieval_status"),
                "confidence": result.get("confidence", 0.0),
            }
        )

    by_category: dict[str, dict[str, float]] = {}
    for category in sorted({str(case["category"]) for case in case_reports}):
        category_cases = [case for case in case_reports if str(case["category"]) == category]
        by_category[category] = _aggregate_case_metrics(category_cases)

    overall_summary = _aggregate_case_metrics(case_reports)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_version": model_version_label or settings.ollama_model,
        "benchmark_path": benchmark_path,
        "case_count": len(case_reports),
        "summary": overall_summary,
        "by_category": by_category,
        "cases": case_reports,
    }


def compare_reports(baseline_report: dict[str, Any], candidate_report: dict[str, Any]) -> dict[str, Any]:
    baseline_summary = baseline_report.get("summary", {})
    candidate_summary = candidate_report.get("summary", {})

    metric_keys = ["faithfulness", "relevance", "correctness", "overall"]
    delta_summary = {
        metric: round(
            float(candidate_summary.get(metric, 0.0)) - float(baseline_summary.get(metric, 0.0)),
            4,
        )
        for metric in metric_keys
    }

    category_names = set(baseline_report.get("by_category", {}).keys()) | set(
        candidate_report.get("by_category", {}).keys()
    )
    by_category_delta: dict[str, dict[str, float]] = {}

    for category in sorted(category_names):
        baseline_category = baseline_report.get("by_category", {}).get(category, {})
        candidate_category = candidate_report.get("by_category", {}).get(category, {})
        by_category_delta[category] = {
            metric: round(
                float(candidate_category.get(metric, 0.0)) - float(baseline_category.get(metric, 0.0)),
                4,
            )
            for metric in metric_keys
        }

    return {
        "baseline_model": baseline_report.get("model_version", "unknown"),
        "candidate_model": candidate_report.get("model_version", "unknown"),
        "delta_summary": delta_summary,
        "delta_by_category": by_category_delta,
    }
