# Synthetic AI extraction benchmark

Run the reproducible local comparison from the project root:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_ai_extraction.py
```

The checked-in dataset, `docs/demo_data/ai_extraction_benchmark.json`, contains 10 hand-authored synthetic documents and 29 labelled field/page pairs. The script compares regex/pattern extraction, the local transformer extractor, and the actual hybrid upload strategy (pattern results take precedence; local AI fills fields not found by patterns). It reports exact field-and-page precision, recall, F1, per-field exact recall, and local document-type classification accuracy.

## Current smoke-evaluation result

On the pinned local models bundled with this prototype:

| Extractor | Predictions | Exact value and page | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Deterministic patterns | 29 | 29 / 29 | 1.0000 | 1.0000 | 1.0000 |
| Local transformer only | 26 | 16 / 29 | 0.6154 | 0.5517 | 0.5818 |
| Pattern + local transformer | 29 | 29 / 29 | 1.0000 | 1.0000 | 1.0000 |

Results were refreshed on 6 November 2026 with the pinned local models installed. The dataset's field layout is close to the deterministic templates, so the pattern row measures the templates rather than general accuracy; the local transformer alone is markedly weaker here and its suggestions only fill fields the patterns miss. The local classifier labelled 9 of the 10 synthetic examples with the expected document type. This tiny, hand-authored set is a functional smoke evaluation, not a representative accuracy claim. It does not cover handwriting, real scans, non-English OCR, authentic third-party submissions, demographic bias, or adversarial documents. Real-world use requires a separately labelled and approved evaluation set.

The optional LLM comparison is intentionally not run by default. To send this synthetic dataset's extracted text to the configured external provider, set `APP_AI_PROVIDER=openai`, `APP_LLM_MODEL`, and `OPENAI_API_KEY`, then explicitly run:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_ai_extraction.py --include-llm --allow-external-synthetic
```

This opt-in transmits synthetic page text externally. The benchmark script refuses the LLM run without both the provider selection and the explicit flag. No live LLM results are claimed until that run is performed. The application does not need an API key to run its core workflow.
