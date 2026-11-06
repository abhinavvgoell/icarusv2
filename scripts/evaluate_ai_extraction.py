"""Evaluate advisory field extraction using the checked-in synthetic dataset."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ai
from app.services import extract_fields_with_pages

DATASET = ROOT / "docs" / "demo_data" / "ai_extraction_benchmark.json"


def _triples(items: list[dict]) -> set[tuple[str, str, int]]:
    return {(item["field_name"], item.get("raw_value", item.get("value", "")).strip(), item["page"])
            for item in items}


def score_cases(cases: list[dict], predictions: dict[str, list[dict]], predicted_types: dict[str, str | None] | None = None) -> dict:
    expected_total = predicted_total = exact = value_correct_page_wrong = 0
    type_total = type_correct = 0
    per_field: dict[str, dict[str, int]] = {}
    for case in cases:
        expected = _triples(case["expected_fields"])
        target_fields = {f for f, _, _ in expected}
        actual = {(f, v, p) for f, v, p in _triples(predictions.get(case["id"], [])) if f in target_fields}
        expected_total += len(expected)
        predicted_total += len(actual)
        exact += len(expected & actual)
        for field, value, page in expected:
            metrics = per_field.setdefault(field, {"expected": 0, "exact": 0})
            metrics["expected"] += 1
            metrics["exact"] += int((field, value, page) in actual)
            if not any(a_field == field and a_value == value for a_field, a_value, _ in actual):
                continue
            if (field, value, page) not in actual:
                value_correct_page_wrong += 1
        if predicted_types is not None:
            type_total += 1
            type_correct += int(predicted_types.get(case["id"]) == case["document_type"])
    precision = exact / predicted_total if predicted_total else 0.0
    recall = exact / expected_total if expected_total else 0.0
    return {
        "documents": len(cases),
        "expected_fields": expected_total,
        "predicted_fields": predicted_total,
        "exact_value_and_page": exact,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "value_correct_page_wrong": value_correct_page_wrong,
        "classification_accuracy": round(type_correct / type_total, 4) if type_total else None,
        "per_field_exact_recall": {
            field: round(values["exact"] / values["expected"], 4)
            for field, values in sorted(per_field.items())
        },
    }


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-llm", action="store_true", help="Call the configured external provider with synthetic text only")
    parser.add_argument("--allow-external-synthetic", action="store_true", help="Required with --include-llm to confirm external transmission")
    args = parser.parse_args()
    if args.include_llm and not args.allow_external_synthetic:
        parser.error("--include-llm requires --allow-external-synthetic because it sends synthetic page text externally")

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if dataset.get("synthetic") is not True or not dataset.get("cases"):
        raise SystemExit("Refusing evaluation: dataset is not explicitly marked synthetic")
    cases = dataset["cases"]
    outputs: dict[str, dict] = {"patterns": {}, "local_transformer": {}, "hybrid_local": {}, "configured_llm_hybrid": {}}
    predictions: dict[str, dict[str, list[dict]]] = {name: {} for name in outputs}
    model = None
    local_types: dict[str, str | None] = {}
    if ai.status()["models_available"]:
        try:
            model = ai.get_engine()
        except Exception as exc:
            print(f"Local transformer unavailable: {type(exc).__name__}", file=sys.stderr)
    llm_enabled = args.include_llm
    if llm_enabled:
        if os.getenv("APP_AI_PROVIDER", "local").lower() != "openai":
            parser.error("Set APP_AI_PROVIDER=openai before --include-llm")
        if not os.getenv("OPENAI_API_KEY") or not os.getenv("APP_LLM_MODEL"):
            parser.error("APP_AI_PROVIDER=openai requires OPENAI_API_KEY and APP_LLM_MODEL")

    for case in cases:
        pages = case["pages"]
        predictions["patterns"][case["id"]] = [
            {"field_name": field, "raw_value": value, "page": page}
            for field, value, page in extract_fields_with_pages(pages)
        ]
        if model:
            predicted_type, _ = model.classify(pages)
            local_fields = model.extract_fields(pages)
            predictions["local_transformer"][case["id"]] = local_fields
            pattern_names = {item["field_name"] for item in predictions["patterns"][case["id"]]}
            predictions["hybrid_local"][case["id"]] = predictions["patterns"][case["id"]] + [
                item for item in local_fields if item["field_name"] not in pattern_names
            ]
            local_types[case["id"]] = predicted_type
        if llm_enabled:
            result = ai.analyze(pages)
            pattern_names = {item["field_name"] for item in predictions["patterns"][case["id"]]}
            predictions["configured_llm_hybrid"][case["id"]] = predictions["patterns"][case["id"]] + [
                item for item in result["fields"] if item["field_name"] not in pattern_names
            ]

    for name, values in predictions.items():
        if name in {"local_transformer", "hybrid_local"} and model is None:
            outputs[name] = {"available": False, "reason": "Local model assets or runtime unavailable"}
            continue
        if name == "configured_llm_hybrid" and not llm_enabled:
            outputs[name] = {"available": False, "reason": "Not run; requires explicit external synthetic-data opt-in"}
            continue
        outputs[name] = {"available": True, **score_cases(cases, values, local_types if name == "local_transformer" else None)}
    print(json.dumps({"dataset_id": dataset["dataset_id"], "synthetic": True, "results": outputs}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
