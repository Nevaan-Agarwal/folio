"""Tesseract OCR service for Folio receipt extraction."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse
from urllib.request import urlopen

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
        try:
            receipt_repository.update_processing_status(receipt_id, "ocr_processing")

            if pytesseract is None or Output is None:
                raise RuntimeError("Tesseract OCR dependencies are unavailable.")
            if self.image_processor is None:
                raise RuntimeError("ImageProcessor is unavailable.")

            source_path, downloaded_temp_path = self._resolve_image_source(image_path, receipt_id)
            preprocessed_path = self.image_processor.preprocess_for_ocr(source_path)

            custom_config = f"--oem 3 --psm 6 -l {language}"
            data = pytesseract.image_to_data(
                preprocessed_path,
                output_type=Output.DICT,
                config=custom_config,
            )
            raw_text = pytesseract.image_to_string(
                preprocessed_path,
                config=custom_config,
            ).strip()

            valid_confidences: list[float] = []
            low_confidence_words: list[dict] = []
            words = data.get("text", [])
            confidences = data.get("conf", [])

            for index, word in enumerate(words):
                cleaned = (word or "").strip()
                if not cleaned:
                    continue
                try:
                    confidence_value = float(confidences[index])
                except (TypeError, ValueError, IndexError):
                    continue
                if confidence_value < 0:
                    continue
                valid_confidences.append(confidence_value)
                if confidence_value < 50:
                    low_confidence_words.append(
                        {
                            "word": cleaned,
                            "confidence": round(confidence_value, 2),
                            "position": index,
                        }
                    )

            overall_confidence = round(mean(valid_confidences), 2) if valid_confidences else 0.0
            language_detected = self.detect_language(raw_text)

            result = {
                "raw_text": raw_text,
                "confidence": overall_confidence,
                "word_count": len([word for word in words if (word or "").strip()]),
                "language_detected": language_detected,
                "low_confidence_words": low_confidence_words,
                "is_readable": overall_confidence >= 60,
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
            return {"error": str(exc), "confidence": 0, "is_readable": False}
        finally:
            self._safe_delete(preprocessed_path)
            self._safe_delete(downloaded_temp_path)

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
