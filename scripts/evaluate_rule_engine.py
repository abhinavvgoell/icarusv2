"""Evaluate the illustrative rule engine against the labelled synthetic corpus.

Runs the real application pipeline (create -> upload -> reviewer pre-check ->
read findings) for every labelled case and scores predicted rule IDs against
the expected rule IDs. All data is synthetic; results describe this demo only.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs" / "demo_data" / "rule_evaluation_cases.json"


def score(case_results: list[dict]) -> dict:
    """Aggregate per-case expected vs predicted rule IDs into micro metrics."""
    per_rule: dict[str, dict[str, int]] = {}
    tp = fp = fn = 0
    for case in case_results:
        expected = set(case["expected_rules"])
        predicted = set(case["predicted_rules"])
        for rule in expected & predicted:
            per_rule.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0})["tp"] += 1
            tp += 1
        for rule in predicted - expected:
            per_rule.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
            fp += 1
        for rule in expected - predicted:
            per_rule.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1
            fn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": len(case_results),
        "expected_rule_instances": tp + fn,
        "predicted_rule_instances": tp + fp,
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "per_rule": {
            rule: {
                **counts,
                "precision": round(counts["tp"] / (counts["tp"] + counts["fp"]), 4) if counts["tp"] + counts["fp"] else 1.0,
                "recall": round(counts["tp"] / (counts["tp"] + counts["fn"]), 4) if counts["tp"] + counts["fn"] else 1.0,
            }
            for rule, counts in sorted(per_rule.items())
        },
        "cases_with_false_positives": sorted(c["id"] for c in case_results if set(c["predicted_rules"]) - set(c["expected_rules"])),
        "cases_with_missed_expected": sorted(c["id"] for c in case_results if set(c["expected_rules"]) - set(c["predicted_rules"])),
    }


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, help="Optional path for the full JSON result")
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if dataset.get("synthetic") is not True or not dataset.get("cases"):
        raise SystemExit("Refusing evaluation: dataset is not explicitly marked synthetic")

    with tempfile.TemporaryDirectory(prefix="arbiter-rule-eval-") as tmp:
        tmp_path = Path(tmp)
        import os
        os.environ["APP_DATABASE_PATH"] = str(tmp_path / "evaluation.db")
        os.environ["APP_UPLOAD_DIR"] = str(tmp_path / "uploads")
        sys.path.insert(0, str(ROOT))
        from fastapi.testclient import TestClient
        from app import ai
        from app.main import app

        client = TestClient(app)
        login = lambda email, password: {"X-Session-Token": client.post("/api/auth/login", json={"email": email, "password": password}).json()["token"]}
        submitter = login("submitter@cascade.demo", "Submitter@123")
        reviewer = login("reviewer@arbiter.demo", "Reviewer@123")

        results = []
        for case in dataset["cases"]:
            created = client.post("/api/applications", headers=submitter, json={
                "organization_name": case["organization_name"],
                "review_cycle": case["review_cycle"],
                "request_type": case["request_type"],
                "scope_name": case.get("scope_name", ""),
                "requested_capacity": case.get("requested_capacity"),
            })
            if created.status_code != 200:
                raise SystemExit(f"{case['id']}: application creation failed {created.status_code} {created.text}")
            application_id = created.json()["id"]
            started = time.perf_counter()
            for document in case["documents"]:
                data = document["text"].encode(document.get("encoding", "utf-8"))
                uploaded = client.post(
                    f"/api/applications/{application_id}/documents?document_type={document['document_type']}",
                    headers=submitter,
                    files={"file": (document["filename"], data, "application/pdf" if document["filename"].endswith(".pdf") else "text/plain")},
                )
                if uploaded.status_code != 200:
                    raise SystemExit(f"{case['id']}: upload failed {uploaded.status_code} {uploaded.text}")
            precheck_started = time.perf_counter()
            checked = client.post(f"/api/applications/{application_id}/precheck", headers=reviewer)
            precheck_seconds = time.perf_counter() - precheck_started
            if checked.status_code != 200:
                raise SystemExit(f"{case['id']}: pre-check failed {checked.status_code} {checked.text}")
            pipeline_seconds = time.perf_counter() - started
            findings = client.get(f"/api/applications/{application_id}", headers=reviewer).json()["findings"]
            predicted = sorted({f["rule_id"] for f in findings if f.get("rule_id")})
            results.append({
                "id": case["id"],
                "expected_rules": case["expected_rules"],
                "predicted_rules": predicted,
                "forbidden_rules": case.get("forbidden_rules", []),
                "forbidden_triggered": sorted(set(predicted) & set(case.get("forbidden_rules", []))),
                "precheck_seconds": round(precheck_seconds, 4),
                "pipeline_seconds": round(pipeline_seconds, 4),
            })

        summary = score(results)
        summary["mean_precheck_seconds"] = round(sum(r["precheck_seconds"] for r in results) / len(results), 4)
        summary["mean_pipeline_seconds"] = round(sum(r["pipeline_seconds"] for r in results) / len(results), 4)
        payload = {
            "dataset_id": dataset["dataset_id"],
            "synthetic": True,
            "ai_status": {"models_available": ai.status().get("models_available"), "provider": ai.status().get("provider")},
            "results": summary,
            "cases": results,
        }
        if args.json_out:
            args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(run())