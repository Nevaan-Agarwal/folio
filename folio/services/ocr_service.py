"""Tesseract OCR service for Folio receipt extraction."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse
from urllib.request import urlopen

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency in constrained envs
    cv2 = None

try:
    import pytesseract
    from pytesseract import Output
except Exception:  # pragma: no cover - dependency optional in some environments
    pytesseract = None
    Output = None

from repositories import receipt_repository

logger = logging.getLogger(__name__)


class OcrService:
    def __init__(self) -> None:
        if pytesseract is not None:
            configured_cmd = os.getenv("TESSERACT_CMD", "").strip()
            candidate_paths = [
                configured_cmd,
                "C:/Program Files/Tesseract-OCR/tesseract.exe",
                "C:/Users/NevaanAgarwal/AppData/Local/Programs/Tesseract-OCR/tesseract.exe",
            ]
            for candidate in candidate_paths:
                if not candidate:
                    continue
                normalized = candidate.replace("\\", "/")
                if Path(normalized).exists():
                    pytesseract.pytesseract.tesseract_cmd = normalized
                    break
        try:
            from utils.image_processor import ImageProcessor

            self.image_processor = ImageProcessor()
        except Exception:
            self.image_processor = None

    def run_ocr(
        self, receipt_id: str, image_path: str, language: str = "eng+deu"
    ) -> dict:
        preprocessed_path = ""
        source_path = image_path
        downloaded_temp_path = ""
        variant_paths: list[str] = []
        try:
            receipt_repository.update_processing_status(receipt_id, "ocr_processing")

            if pytesseract is None or Output is None:
                raise RuntimeError("Tesseract OCR dependencies are unavailable.")
            if self.image_processor is None:
                raise RuntimeError("ImageProcessor is unavailable.")

            source_path, downloaded_temp_path = self._resolve_image_source(image_path, receipt_id)
            preprocessed_path = self.image_processor.preprocess_for_ocr(source_path)
            variant_paths = self._build_ocr_variants(
                receipt_id=receipt_id,
                source_path=source_path,
                preprocessed_path=preprocessed_path,
            )

            candidates: list[dict] = []
            for variant in variant_paths:
                for psm in (4, 6, 11):
                    candidates.append(self._run_ocr_pass(variant, language=language, psm=psm))

            best_result = max(candidates, key=lambda item: item["confidence"])
            raw_text = best_result["raw_text"]
            words = best_result["words"]
            overall_confidence = best_result["confidence"]
            low_confidence_words = best_result["low_confidence_words"]
            language_detected = self.detect_language(raw_text)

            result = {
                "raw_text": raw_text,
                "confidence": overall_confidence,
                "word_count": len([word for word in words if (word or "").strip()]),
                "language_detected": language_detected,
                "low_confidence_words": low_confidence_words,
                "is_readable": overall_confidence >= 55,
            }

            receipt_repository.update_receipt(
                receipt_id,
                {
                    "ocrText": raw_text,
                    "ocrConfidence": overall_confidence,
                    "processingStatus": "ocr_complete",
                },
            )
            return result
        except Exception as exc:
            logger.exception("OCR failed for receipt %s", receipt_id)
            receipt_repository.update_processing_status(receipt_id, "error")
            if "TesseractNotFoundError" in type(exc).__name__:
                cmd_value = ""
                if pytesseract is not None:
                    cmd_value = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
                return {
                    "error": (
                        "Tesseract executable not found. "
                        "Set TESSERACT_CMD in .env (for example: "
                        "C:/Users/NevaanAgarwal/AppData/Local/Programs/Tesseract-OCR/tesseract.exe). "
                        f"Current resolved command: {cmd_value or 'unset'}"
                    ),
                    "confidence": 0,
                    "is_readable": False,
                }
            return {"error": str(exc), "confidence": 0, "is_readable": False}
        finally:
            self._safe_delete(preprocessed_path)
            self._safe_delete(downloaded_temp_path)
            for variant_path in variant_paths:
                if variant_path not in {preprocessed_path, source_path, downloaded_temp_path}:
                    self._safe_delete(variant_path)

    def _run_ocr_pass(self, image_path: str, *, language: str, psm: int) -> dict:
        custom_config = (
            f"--oem 3 --psm {psm} -l {language} "
            "-c preserve_interword_spaces=1 "
            "-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,:-/€$%() "
        )
        data = pytesseract.image_to_data(
            image_path,
            output_type=Output.DICT,
            config=custom_config,
        )
        raw_text = pytesseract.image_to_string(
            image_path,
            config=custom_config,
        ).strip()
        words = data.get("text", [])
        confidences = data.get("conf", [])

        valid_confidences: list[float] = []
        low_confidence_words: list[dict] = []
        for index, word in enumerate(words):
            cleaned = (word or "").strip()
            if not cleaned:
                continue
            # Ignore pure punctuation and one-letter OCR fragments; they skew scores down.
            if len(cleaned) < 2 or not re.search(r"[A-Za-z0-9]", cleaned):
                continue
            try:
                confidence_value = float(confidences[index])
            except (TypeError, ValueError, IndexError):
                continue
            if confidence_value < 0:
                continue
            valid_confidences.append(confidence_value)
            if confidence_value < 45:
                low_confidence_words.append(
                    {
                        "word": cleaned,
                        "confidence": round(confidence_value, 2),
                        "position": index,
                    }
                )

        base_confidence = round(mean(valid_confidences), 2) if valid_confidences else 0.0
        token_count = len((raw_text or "").split())
        completeness_bonus = 5.0 if token_count >= 20 else (2.5 if token_count >= 10 else 0.0)
        semantic_bonus = self._semantic_signal_bonus(raw_text)
        boosted_confidence = min(100.0, round(base_confidence + completeness_bonus + semantic_bonus, 2))

        return {
            "raw_text": raw_text,
            "words": words,
            "confidence": boosted_confidence,
            "low_confidence_words": low_confidence_words,
        }

    def _semantic_signal_bonus(self, text: str) -> float:
        source = (text or "").lower()
        score = 0.0
        if re.search(r"\b(total|summe|gesamt|betrag)\b", source):
            score += 1.5
        if re.search(r"\b(vat|mwst|tax)\b", source):
            score += 1.0
        if re.search(r"\b\d{2}[./-]\d{2}[./-]\d{2,4}\b", source):
            score += 1.0
        if re.search(r"(€|\$|eur|usd)", source):
            score += 1.0
        if re.search(r"\b(receipt|beleg|invoice|rechnung)\b", source):
            score += 1.0
        return min(score, 5.0)

    def _build_ocr_variants(
        self, *, receipt_id: str, source_path: str, preprocessed_path: str
    ) -> list[str]:
        variants = [preprocessed_path]
        if cv2 is None:
            return variants
        source_image = cv2.imread(source_path, cv2.IMREAD_GRAYSCALE)
        if source_image is None:
            return variants

        try:
            normalized = cv2.normalize(source_image, None, 0, 255, cv2.NORM_MINMAX)
            otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            closed = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel)
            variant_path = Path(tempfile.gettempdir()) / f"folio_{receipt_id}_ocr_variant.png"
            cv2.imwrite(str(variant_path), closed)
            variants.append(str(variant_path))
        except Exception:
            return variants
        return variants

    def detect_language(self, text: str) -> str:
        german_words = {"der", "die", "das", "und", "mit", "für", "rechnung", "betrag", "mwst"}
        tokens = [token.strip(".,:;!?").lower() for token in (text or "").split()]
        german_count = sum(1 for token in tokens if token in german_words)
        return "deu" if german_count > 3 else "eng"

    def _resolve_image_source(self, image_path: str, receipt_id: str) -> tuple[str, str]:
        parsed = urlparse(image_path)
        if parsed.scheme in {"http", "https"}:
            extension = Path(parsed.path).suffix or ".png"
            temp_path = Path(tempfile.gettempdir()) / f"folio_{receipt_id}_source{extension}"
            with urlopen(image_path, timeout=20) as response:
                temp_path.write_bytes(response.read())
            return str(temp_path), str(temp_path)
        return image_path, ""

    def _safe_delete(self, path: str) -> None:
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
