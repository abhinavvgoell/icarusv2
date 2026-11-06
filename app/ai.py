"""Optional, on-device document assistance using pinned ONNX models.

AI output is advisory. It never selects regulatory rules or makes a compliance
decision. Document contents stay on the host; model assets are downloaded
separately by the explicit setup script.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

MODEL_ROOT = Path(os.getenv("APP_AI_MODEL_DIR", Path(__file__).resolve().parents[1] / "data" / "models" / "icarus-ai"))
CLASSIFIER_MODEL = "sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
EXTRACTOR_MODEL = "Xenova/distilbert-base-cased-distilled-squad@bdbb0a5e9c617c3c7ea9f3cbaf2be3ad871bcd7d"
CLASS_LABELS = {
    "declaration": "Signed declaration or undertaking by an authorized representative",
    "safety_certificate": "Safety certificate or fire clearance issued by an authority",
    "prior_approval": "Previously issued approval, licence or registration letter",
    "staff_declaration": "Responsible staff list, qualification statement or appointments",
    "consent_letter": "Partner, landlord or authority consent letter",
    "facility_declaration": "Facility, premises or site declaration with layout details",
}
FIELD_QUESTIONS = {
    "organization_name": "What is the name of the organization or entity stated in this document?",
    "certificate_number": "What is the certificate number or reference number stated in this document?",
    "valid_until": "What date does this document say it is valid until or expires?",
    "scope_name": "What product, service or category is this document about or approved for?",
    "capacity": "What is the approved or sanctioned capacity number stated in this document?",
}
FIELD_PAGE_HINTS = {
    "organization_name": re.compile(r"\b(organization|entity|company|issued|applicant)\b", re.I),
    "certificate_number": re.compile(r"\b(certificate|reference|serial|number|registration)\b", re.I),
    "valid_until": re.compile(r"\b(valid|validity|expiry|expires?|expiration|until|through)\b", re.I),
    "scope_name": re.compile(r"\b(scope|service|product|category|approved\s+for|operations?)\b", re.I),
    "capacity": re.compile(r"\b(capacity|limit|quota|sanctioned)\b", re.I),
}
MAX_SEQUENCE_LENGTH = 384
MIN_FIELD_MODEL_SCORE = 0.15

# Conservative plausibility guards for model-suggested values. A question-answering
# model can confidently answer with unrelated page text when a field is absent, so
# every suggestion must still look like the field it claims to be.
ORG_LIKE = re.compile(r"\b(ltd|limited|pvt|private|llc|inc|incorporated|company|corp|plc|"
                      r"institute|university|college|foundation|trust|limited)\b", re.I)
SCOPE_STOPWORDS = re.compile(r"^(?:n/?a|none|not (?:stated|applicable|available)|unknown|nil|"
                             r"name|address|date|signature|declaration|certificate)\b", re.I)

def _plausible_scope_name(value: str) -> bool:
    """Reject values that are clearly a label, a date, an address or an entity name."""
    value = value.strip()
    if not value or len(value) > 120 or "\n" in value or len(value.split()) > 12:
        return False
    if not re.search(r"[A-Za-z]", value) or SCOPE_STOPWORDS.search(value):
        return False
    if ORG_LIKE.search(value):
        return False
    return True


class AIUnavailable(RuntimeError):
    pass


def assistance_attempted() -> bool:
    """True when the configured pipeline actually tried to analyse the document.

    A missing optional model install is an environment condition, not a defect in
    the submission, so findings must not be raised merely because assistance is
    unavailable in this deployment.
    """
    current = status()
    if current["models_available"]:
        return True
    return bool(current["enabled"] and current["provider"] == "openai" and current["external_processing"])


def model_assets_present() -> bool:
    return all((MODEL_ROOT / path).is_file() for path in (
        "classify/tokenizer.json", "classify/model_O4.onnx",
        "qa/tokenizer.json", "qa/model_int8.onnx",
    ))


def status() -> dict:
    enabled = os.getenv("APP_AI_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    present = model_assets_present()
    provider = os.getenv("APP_AI_PROVIDER", "local").strip().lower()
    external_processing = enabled and provider == "openai" and bool(os.getenv("OPENAI_API_KEY"))
    if not enabled:
        label = "AI assistance disabled by configuration"
    elif provider == "openai" and external_processing:
        label = "Optional OpenAI LLM extraction enabled; document text is sent externally"
    elif provider == "openai":
        label = "OpenAI LLM selected but credentials are missing; local/manual fallback active"
    elif not present:
        label = "Local AI models not installed; manual extraction path active"
    else:
        label = "Local AI models available; inference runs on this machine"
    return {"enabled": enabled, "models_available": present, "label": label,
            "classifier_model": CLASSIFIER_MODEL, "extractor_model": EXTRACTOR_MODEL,
            "provider": provider, "external_processing": external_processing}


class LocalDocumentAI:
    def __init__(self):
        if not model_assets_present():
            raise AIUnavailable("Local AI model assets are missing")
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise AIUnavailable("Install the optional onnxruntime and tokenizers packages") from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.classifier = ort.InferenceSession(str(MODEL_ROOT / "classify" / "model_O4.onnx"), options, providers=["CPUExecutionProvider"])
        self.extractor = ort.InferenceSession(str(MODEL_ROOT / "qa" / "model_int8.onnx"), options, providers=["CPUExecutionProvider"])
        self.classifier_tokenizer = Tokenizer.from_file(str(MODEL_ROOT / "classify" / "tokenizer.json"))
        self.extractor_tokenizer = Tokenizer.from_file(str(MODEL_ROOT / "qa" / "tokenizer.json"))
        self._label_vectors: dict[str, list[float]] = {}

    @staticmethod
    def _inputs(session, encoding):
        import numpy as np
        values = {
            "input_ids": np.asarray([encoding.ids], dtype=np.int64),
            "attention_mask": np.asarray([encoding.attention_mask], dtype=np.int64),
            "token_type_ids": np.asarray([encoding.type_ids], dtype=np.int64),
        }
        return {node.name: values[node.name] for node in session.get_inputs() if node.name in values}

    @staticmethod
    def _embedding(session, encoding) -> list[float]:
        import numpy as np
        output = session.run(None, LocalDocumentAI._inputs(session, encoding))[0]
        vectors = np.asarray(output, dtype=np.float32)
        if vectors.ndim == 3:
            mask = np.asarray(encoding.attention_mask, dtype=np.float32)[None, :, None]
            vector = (vectors * mask).sum(axis=1)[0] / max(float(mask.sum()), 1.0)
        else:
            vector = vectors.reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not norm:
            raise AIUnavailable("The local classifier returned an empty embedding")
        return (vector / norm).tolist()

    def classify(self, pages: list[str]) -> tuple[str | None, float]:
        import numpy as np
        text = "\n".join(p.strip() for p in pages if p.strip())[:6000]
        if not text:
            return None, 0.0
        encoding = self.classifier_tokenizer.encode(text)
        if len(encoding.ids) > 256:
            encoding.truncate(256)
        document_vector = np.asarray(self._embedding(self.classifier, encoding), dtype=np.float32)
        scores = {}
        for label, description in CLASS_LABELS.items():
            if label not in self._label_vectors:
                label_encoding = self.classifier_tokenizer.encode(description)
                self._label_vectors[label] = self._embedding(self.classifier, label_encoding)
            scores[label] = float(np.dot(document_vector, np.asarray(self._label_vectors[label], dtype=np.float32)))
        winner = max(scores, key=scores.get)
        similarity = scores[winner]
        # Cosine similarity is a ranking score, not a calibrated probability.
        return (winner if similarity >= 0.25 else None), similarity

    def extract_fields(self, pages: list[str]) -> list[dict]:
        import numpy as np
        extracted = []
        for field, question in FIELD_QUESTIONS.items():
            best = None
            for page_number, page in enumerate(pages, start=1):
                context = page.strip()
                if not context or not FIELD_PAGE_HINTS[field].search(context):
                    continue
                # Keep each ONNX request bounded and preserve the original page.
                context = context[:5000]
                encoding = self.extractor_tokenizer.encode(question, context)
                if len(encoding.ids) > MAX_SEQUENCE_LENGTH:
                    encoding.truncate(MAX_SEQUENCE_LENGTH)
                outputs = self.extractor.run(None, self._inputs(self.extractor, encoding))
                names = [node.name for node in self.extractor.get_outputs()]
                logits = {name: np.asarray(value).reshape(-1) for name, value in zip(names, outputs)}
                start_logits = logits.get("start_logits", outputs[0].reshape(-1))
                end_logits = logits.get("end_logits", outputs[1].reshape(-1))
                sequence_ids = encoding.sequence_ids
                offsets = encoding.offsets
                context_tokens = [i for i, sequence_id in enumerate(sequence_ids) if sequence_id == 1 and offsets[i][1] > offsets[i][0]]
                if not context_tokens:
                    continue
                start_prob = _softmax(start_logits)
                end_prob = _softmax(end_logits)
                for start in context_tokens:
                    max_end = min(start + 20, context_tokens[-1])
                    for end in range(start, max_end + 1):
                        if sequence_ids[end] != 1:
                            continue
                        score = float(start_prob[start] * end_prob[end])
                        if best is None or score > best["score"]:
                            begin_offset, end_offset = offsets[start][0], offsets[end][1]
                            answer = context[begin_offset:end_offset].strip(" \t\r\n.,;:")
                            if answer:
                                line_start = context.rfind("\n", 0, begin_offset)
                                line_start = 0 if line_start == -1 else line_start + 1
                                line_end = context.find("\n", end_offset)
                                line_end = len(context) if line_end == -1 else line_end
                                quote = context[line_start:line_end].strip() or answer
                                best = {"field_name": field, "raw_value": answer, "normalized_value": answer,
                                        "page": page_number, "confidence": score, "source": "local_ai_qa", "score": score,
                                        "quote": quote}
            # This is a conservative display threshold, not a guarantee of correctness.
            if best and best["score"] >= MIN_FIELD_MODEL_SCORE:
                if field == "certificate_number" and not re.search(r"\d", best["raw_value"]):
                    continue
                if field == "valid_until" and not re.search(r"\b\d{4}\b", best["raw_value"]):
                    continue
                if field == "scope_name" and not _plausible_scope_name(best["raw_value"]):
                    continue
                if field == "capacity" and not re.fullmatch(r"\d{1,6}", best["raw_value"].strip()):
                    continue
                best.pop("score", None)
                extracted.append(best)
        return extracted


def _softmax(values):
    import numpy as np
    values = np.asarray(values, dtype=np.float64)
    values = np.exp(values - np.max(values))
    return values / max(float(values.sum()), 1e-12)


@functools.lru_cache(maxsize=1)
def get_engine() -> LocalDocumentAI:
    if not status()["enabled"]:
        raise AIUnavailable("AI assistance is disabled by configuration")
    return LocalDocumentAI()


def analyze(pages: list[str]) -> dict:
    current = status()
    if not current["enabled"] or not current["models_available"]:
        if current["enabled"] and current["provider"] == "openai" and current["external_processing"]:
            try:
                from .llm import OpenAIResponsesExtractor
                fields = OpenAIResponsesExtractor().extract_fields(pages)
                return {"status": "complete" if fields else "manual_review", "suggested_type": None,
                        "similarity": None, "fields": fields,
                        "detail": "External LLM field suggestions; verify every value against the original document.",
                        "model": os.getenv("APP_LLM_MODEL", "configured LLM")}
            except Exception as exc:
                return {"status": "manual_review", "suggested_type": None, "similarity": None,
                        "fields": [], "detail": f"External LLM unavailable; manual review required ({type(exc).__name__}).",
                        "model": None}
        return {"status": "manual_review", "suggested_type": None, "similarity": None,
                "fields": [], "detail": current["label"], "model": None}
    try:
        engine = get_engine()
        suggested_type, similarity = engine.classify(pages)
        fields = engine.extract_fields(pages)
        model = f"{CLASSIFIER_MODEL}; {EXTRACTOR_MODEL}"
        detail = "AI suggestions only; verify against the document."
        if current["provider"] == "openai":
            try:
                from .llm import OpenAIResponsesExtractor
                llm_fields = OpenAIResponsesExtractor().extract_fields(pages)
                known = {field["field_name"] for field in llm_fields}
                fields = llm_fields + [field for field in fields if field["field_name"] not in known]
                model += f"; {os.getenv('APP_LLM_MODEL', 'configured LLM')}"
                detail = "LLM suggestions are external and evidence-checked against source text; verify manually."
            except Exception as exc:
                detail = f"External LLM unavailable ({type(exc).__name__}); local suggestions only. Verify manually."
        return {"status": "complete" if suggested_type or fields else "manual_review",
                "suggested_type": suggested_type, "similarity": similarity if suggested_type else None,
                "fields": fields, "detail": detail if suggested_type or fields else "AI did not produce a reliable suggestion; manual review required.",
                "model": model}
    except Exception as exc:
        return {"status": "manual_review", "suggested_type": None, "similarity": None,
                "fields": [], "detail": f"Local AI unavailable; manual review required ({type(exc).__name__}).", "model": None}
