# Evaluation status and protocol

**Last local synthetic run:** 6 November 2026, with the pinned local models installed. The numbers below describe only the checked-in invented data and this development machine. They are not production accuracy, legal compliance, fraud detection, or time-savings claims.

## Measured demo results

| Component | Dataset and result | Limitation |
| --- | --- | --- |
| Hybrid field extraction | 10 synthetic documents, 29 labelled fields/page references; 29 exact field-page matches from 29 predictions; precision 1.0000, recall 1.0000, F1 1.0000. | No representative real or multilingual/scanned corpus. The hybrid inherits the pattern row's template fit, so this is a regression check rather than an accuracy estimate. |
| Local transformer extraction | Same 10 documents; 16 exact matches from 26 predictions; precision 0.6154, recall 0.5517, F1 0.5818; document-type accuracy 0.9000 on 10 items. | Tiny hand-authored set; the pinned general-purpose model is the weakest extractor here and its output requires human review. |
| Deterministic field patterns | 10 synthetic documents, 29 labelled fields; 29 exact value-and-page matches from 29 predictions; precision 1.0000, recall 1.0000. | The fixtures were authored against these patterns, so this is a regression check, not an accuracy estimate. Templates are narrow and layout variation is limited. |
| Illustrative rule engine | 11 synthetic application cases, 12 expected rule instances; 12 predictions, 0 false positives, 0 misses; micro precision/recall/F1 = 1.0000. | Labels were authored for this small fixture set; score is not an estimate of performance on real applications. |
| Rule processing time | In the 6 November 2026 local run, mean pre-check 0.0207 s and mean end-to-end synthetic case pipeline 0.3707 s. | Single local run on a development machine; no load, network, or user-time study. Do not claim time saved. |
| External source verification | No external ground truth or authorized external source connector is available. Existing tests check the six status values and reject internally inconsistent comparisons. | Status correctness cannot be measured against an authoritative external source until an approved integration and labeled external source outcomes are supplied. |
| Infrastructure visual assistance | No computer-vision model is active. Image uploads are routed to reviewer visual review. | No visual model precision/recall or inspection recommendation score exists. |

## Reproduction

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_ai_extraction.py
.\.venv\Scripts\python.exe scripts\evaluate_rule_engine.py
```

Both datasets must remain explicitly marked synthetic. The optional LLM benchmark sends synthetic text externally only when both the provider and explicit external-data opt-in are selected; it is not part of these results.

## Requirements for a representative evaluation

Before claiming real-world accuracy, assemble an authorized and minimized corpus with independent labels from qualified reviewers. Preserve organization and document confidentiality; exclude unapproved personal data. Split by organization and document family so near-duplicate templates cannot leak between tuning and evaluation. Record the applicable review cycle, application category, official source version, expected fields and page/evidence locations, expected rule outcomes, external source ground truth, and processing environment. Report per-field and per-category precision/recall, false-positive and false-negative counts, calibration/review rates, OCR failure rates, external verification status correctness, latency distributions and confidence intervals. Keep all human-adjudicated disagreements in the report. Do not treat synthetic fixtures as evidence for a regulatory decision.
