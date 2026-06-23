"""GPT-5.4 receipt extraction service using OCR text only."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from statistics import mean
from typing import Any

import httpx

from middleware.auth_middleware import sanitize_ocr_text
from prompts.ai_prompts import (
    ALLOWED_EXPENSE_CATEGORIES,
    EXTRACTION_PROMPT,
    SYSTEM_PROMPT,
)
from repositories import audit_repository, form_repository, receipt_repository

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - dependency optional in some environments
    OpenAI = None

logger = logging.getLogger(__name__)


class AiService:
    REQUIRED_KEYS = {
        "merchant",
        "address",
        "receiptNumber",
        "date",
        "currency",
        "subtotal",
        "tax",
        "tip",
        "total",
        "expenseCategory",
        "tagDerBewirtung",
        "ortDerBewirtung",
        "anlasDerBewirtung",
        "suggestedDescription",
        "language",
        "confidence",
        "missingFields",
        "rawDataUsed",
    }

    NULLABLE_FIELDS = {
        "merchant",
        "address",
        "receiptNumber",
        "date",
        "currency",
        "subtotal",
        "tax",
        "tip",
        "total",
        "tagDerBewirtung",
        "ortDerBewirtung",
        "anlasDerBewirtung",
        "suggestedDescription",
    }

    DATE_FIELDS = {"date", "tagDerBewirtung"}
    AMOUNT_FIELDS = {"subtotal", "tax", "tip", "total"}

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.4",
        client=None,
        sleep_fn=time.sleep,
    ) -> None:
        resolved_key = api_key or os.getenv("OPENAI_API_KEY", "")
        if client is not None:
            self._client = client
        elif OpenAI is not None and resolved_key:
            self._client = OpenAI(
                api_key=resolved_key,
                http_client=httpx.Client(trust_env=False),
            )
        else:
            self._client = None
        self._model = model
        self._sleep = sleep_fn

    def process_receipt(self, receipt_id: str, ocr_text: str) -> dict:
        receipt_repository.update_processing_status(receipt_id, "ai_processing")
        try:
            if not (ocr_text or "").strip():
                error_result = self._build_error_result("OCR text is empty.")
                receipt_repository.update_processing_status(receipt_id, "error")
                return error_result

            if self._client is None:
                raise RuntimeError("OpenAI client unavailable.")

            sanitized_text = sanitize_ocr_text(ocr_text)
            if not sanitized_text.strip():
                error_result = self._build_error_result("OCR text is empty after sanitization.")
                receipt_repository.update_processing_status(receipt_id, "error")
                return error_result
            prompt = EXTRACTION_PROMPT.replace("{ocr_text}", sanitized_text)

            # SECURITY: only OCR text goes to the model; no image payloads.
            response = self._call_with_rate_limit_retry(prompt)
            usage = getattr(response, "usage", None)

            content = response.choices[0].message.content if response.choices else "{}"
            parsed = self._safe_parse_json(content)
            validated = self._validate_and_normalize(parsed)
            validated = self._enrich_with_ocr_fallback(validated, sanitized_text)

            receipt_repository.update_receipt(
                receipt_id,
                {
                    "merchant": validated["merchant"],
                    "address": validated["address"],
                    "date": validated["date"],
                    "currency": validated["currency"],
                    "subtotal": validated["subtotal"],
                    "tax": validated["tax"],
                    "tip": validated["tip"],
                    "total": validated["total"],
                    "receiptNumber": validated["receiptNumber"],
                    "processingStatus": "awaiting_review",
                },
            )

            user_id = self._resolve_receipt_user_id(receipt_id)
            form_repository.create_form_from_ai_result(receipt_id, user_id, validated)
            self._log_token_usage(receipt_id, usage)
            return validated
        except Exception as exc:
            logger.exception("AI processing failed for receipt %s", receipt_id)
            receipt_repository.update_processing_status(receipt_id, "error")
            return self._build_error_result(str(exc))

    def _call_with_rate_limit_retry(self, prompt: str):
        attempts = 0
        while attempts < 2:
            attempts += 1
            try:
                return self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
            except Exception as exc:
                message = str(exc).lower()
                is_rate_limited = "rate limit" in message or "429" in message
                if attempts < 2 and is_rate_limited:
                    self._sleep(5)
                    continue
                raise

    def _resolve_receipt_user_id(self, receipt_id: str) -> str:
        try:
            receipt = receipt_repository.get_receipt(receipt_id)
        except Exception:
            return ""
        return receipt.userId if receipt is not None else ""

    def _safe_parse_json(self, content: str) -> dict:
        try:
            return json.loads(content)
        except Exception as exc:
            raise ValueError("AI returned invalid JSON.") from exc

    def _validate_and_normalize(self, payload: dict[str, Any]) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("AI result must be an object.")

        result = dict(payload)
        for key in self.REQUIRED_KEYS:
            result.setdefault(key, None if key in self.NULLABLE_FIELDS else "")

        category = result.get("expenseCategory")
        if category not in ALLOWED_EXPENSE_CATEGORIES:
            result["expenseCategory"] = "Other"

        for key in self.DATE_FIELDS:
            value = result.get(key)
            if value is None:
                continue
            if not self._is_iso_date(value):
                result[key] = None

        for key in self.AMOUNT_FIELDS:
            value = result.get(key)
            if value is None:
                continue
            try:
                result[key] = float(value)
            except (TypeError, ValueError):
                result[key] = None

        if result.get("language") not in {"de", "en"}:
            result["language"] = "en"

        confidence = result.get("confidence")
        if not isinstance(confidence, dict):
            confidence = {}
        result["confidence"] = {
            "overall": self._clamp_confidence(confidence.get("overall")),
            "merchant": self._clamp_confidence(confidence.get("merchant")),
            "total": self._clamp_confidence(confidence.get("total")),
            "date": self._clamp_confidence(confidence.get("date")),
        }

        missing_fields = [key for key in self.NULLABLE_FIELDS if result.get(key) is None]
        result["missingFields"] = missing_fields
        if not isinstance(result.get("rawDataUsed"), str):
            result["rawDataUsed"] = ""

        return result

    def _enrich_with_ocr_fallback(self, payload: dict[str, Any], ocr_text: str) -> dict[str, Any]:
        lines = [line.strip() for line in (ocr_text or "").splitlines() if line.strip()]
        text = "\n".join(lines)
        lowered = text.lower()

        def parse_amount(value: str) -> float | None:
            cleaned = (value or "").strip().replace(" ", "")
            if not cleaned:
                return None
            cleaned = re.sub(r"[^\d,.\-]", "", cleaned)
            if cleaned.count(",") > 0 and cleaned.count(".") > 0:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif cleaned.count(",") > 0 and cleaned.count(".") == 0:
                cleaned = cleaned.replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return None

        def extract_line_amount(line: str, label_patterns: list[str]) -> float | None:
            normalized_line = line.strip()
            for label_pattern in label_patterns:
                label_first = re.search(
                    rf"{label_pattern}[^\d\-+€$£]*([€$£]?\s*[0-9][0-9., ]*)",
                    normalized_line,
                    flags=re.IGNORECASE,
                )
                if label_first:
                    amount = parse_amount(label_first.group(1))
                    if amount is not None and amount >= 0:
                        return amount
                amount_first = re.search(
                    rf"([€$£]?\s*[0-9][0-9., ]*)[^\w\d]+{label_pattern}",
                    normalized_line,
                    flags=re.IGNORECASE,
                )
                if amount_first:
                    amount = parse_amount(amount_first.group(1))
                    if amount is not None and amount >= 0:
                        return amount
            return None

        def extract_labeled_amount(label_patterns: list[str], *, excluded_patterns: list[str] | None = None) -> float | None:
            excluded_patterns = excluded_patterns or []
            for line in lines:
                if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in excluded_patterns):
                    continue
                amount = extract_line_amount(line, label_patterns)
                if amount is not None:
                    return amount
            for pattern in label_patterns:
                if any(pattern == excluded for excluded in excluded_patterns):
                    continue
                match = re.search(
                    rf"{pattern}[^\d\-+€$£]*([€$£]?\s*[0-9][0-9., ]*)",
                    text,
                    flags=re.IGNORECASE,
                )
                if not match:
                    continue
                amount = parse_amount(match.group(1))
                if amount is not None and amount >= 0:
                    return amount
            return None

        labeled_amounts = {
            "subtotal": extract_labeled_amount(
                [r"\bsubtotal\b", r"\bsub\s*total\b", r"\bnetto\b", r"\bzwischensumme\b"]
            ),
            "tax": extract_labeled_amount(
                [r"\btax\b", r"\bvat\b", r"\bmwst\.?\b", r"\bust\.?\b"]
            ),
            "tip": extract_labeled_amount(
                [r"\btip\b", r"\btrinkgeld\b"]
            ),
            "total": extract_labeled_amount(
                [r"\btotal\b", r"\bgrand\s+total\b", r"\bgesamt(?:betrag)?\b", r"\bsumme\b"],
                excluded_patterns=[r"\bsubtotal\b", r"\bsub\s*total\b"],
            ),
        }
        for field, amount in labeled_amounts.items():
            if amount is not None:
                payload[field] = amount

        if payload.get("total") is None:
            parts = [payload.get("subtotal"), payload.get("tax"), payload.get("tip")]
            if payload.get("subtotal") is not None and any(value is not None for value in parts[1:]):
                payload["total"] = round(sum(float(value or 0) for value in parts), 2)

        if payload.get("date") is None:
            iso_match = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
            de_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", text)
            if iso_match:
                year, month, day = iso_match.groups()
                payload["date"] = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            elif de_match:
                day, month, year = de_match.groups()
                payload["date"] = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            if payload.get("tagDerBewirtung") is None:
                payload["tagDerBewirtung"] = payload.get("date")

        if payload.get("receiptNumber") is None:
            match = re.search(
                r"(?:receipt|invoice|beleg|bon|rechnung|rechnungsnr\.?|belegnr\.?)\s*[:#]?\s*([A-Z0-9\-]{3,})",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                payload["receiptNumber"] = match.group(1)

        if payload.get("currency") is None:
            if "€" in text or " eur" in lowered:
                payload["currency"] = "EUR"
            elif "$" in text or " usd" in lowered:
                payload["currency"] = "USD"
            elif "£" in text or " gbp" in lowered:
                payload["currency"] = "GBP"

        if payload.get("merchant") is None and lines:
            skip_terms = {
                "total",
                "summe",
                "betrag",
                "receipt",
                "invoice",
                "rechnung",
                "mwst",
                "tax",
                "date",
                "beleg",
            }
            for line in lines[:5]:
                normalized = line.lower()
                if len(line) < 3 or any(term in normalized for term in skip_terms):
                    continue
                if re.search(r"[A-Za-z]", line):
                    payload["merchant"] = line[:80]
                    break

        if payload.get("address") is None:
            for line in lines:
                if re.search(r"\b(str|strasse|road|rd|street|st|platz|allee)\b", line, flags=re.IGNORECASE):
                    payload["address"] = line[:120]
                    break

        if payload.get("ortDerBewirtung") is None:
            payload["ortDerBewirtung"] = payload.get("address")

        if payload.get("suggestedDescription") is None:
            merchant = payload.get("merchant")
            total = payload.get("total")
            if merchant and total is not None:
                payload["suggestedDescription"] = f"Business meal at {merchant} ({total:.2f} {payload.get('currency') or 'EUR'})."

        if payload.get("language") not in {"de", "en"}:
            payload["language"] = "de" if any(token in lowered for token in ("rechnung", "mwst", "betrag")) else "en"

        if payload.get("expenseCategory") in (None, "", "Other"):
            payload["expenseCategory"] = "Restaurant" if any(
                token in lowered for token in ("restaurant", "cafe", "bewirtung", "gaststatte")
            ) else "Other"

        confidence = dict(payload.get("confidence") or {})
        if payload.get("total") is not None and confidence.get("total", 0) < 0.7:
            confidence["total"] = 0.78
        if payload.get("merchant") and confidence.get("merchant", 0) < 0.65:
            confidence["merchant"] = 0.72
        if payload.get("date") and confidence.get("date", 0) < 0.65:
            confidence["date"] = 0.7
        if confidence.get("overall", 0) < 0.5:
            confidence["overall"] = round(
                max(
                    confidence.get("overall", 0),
                    mean(
                        [
                            float(confidence.get("merchant", 0)),
                            float(confidence.get("total", 0)),
                            float(confidence.get("date", 0)),
                        ]
                    ),
                ),
                2,
            )
        payload["confidence"] = {
            "overall": self._clamp_confidence(confidence.get("overall")),
            "merchant": self._clamp_confidence(confidence.get("merchant")),
            "total": self._clamp_confidence(confidence.get("total")),
            "date": self._clamp_confidence(confidence.get("date")),
        }

        payload["missingFields"] = [key for key in self.NULLABLE_FIELDS if payload.get(key) is None]
        return payload

    def _is_iso_date(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def _clamp_confidence(self, value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))

    def _log_token_usage(self, receipt_id: str, usage: Any) -> None:
        if usage is None:
            return
        audit_repository.log_event(
            {
                "action": "ai_token_usage",
                "receiptId": receipt_id,
                "promptTokens": getattr(usage, "prompt_tokens", 0),
                "completionTokens": getattr(usage, "completion_tokens", 0),
                "totalTokens": getattr(usage, "total_tokens", 0),
            }
        )

    def _build_error_result(self, error_message: str) -> dict:
        return {
            "error": error_message,
            "merchant": None,
            "address": None,
            "receiptNumber": None,
            "date": None,
            "currency": None,
            "subtotal": None,
            "tax": None,
            "tip": None,
            "total": None,
            "expenseCategory": "Other",
            "tagDerBewirtung": None,
            "ortDerBewirtung": None,
            "anlasDerBewirtung": None,
            "suggestedDescription": None,
            "language": "en",
            "confidence": {"overall": 0.0, "merchant": 0.0, "total": 0.0, "date": 0.0},
            "missingFields": sorted(self.NULLABLE_FIELDS),
            "rawDataUsed": "",
        }


AIService = AiService
