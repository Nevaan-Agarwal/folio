"""GPT-5.4 receipt extraction service using OCR text only."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any

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
            self._client = OpenAI(api_key=resolved_key)
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

            form_repository.create_form_from_ai_result(receipt_id, validated)
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
                    max_tokens=1000,
                    temperature=0.1,
                )
            except Exception as exc:
                message = str(exc).lower()
                is_rate_limited = "rate limit" in message or "429" in message
                if attempts < 2 and is_rate_limited:
                    self._sleep(5)
                    continue
                raise

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
