"""OpenCV-based preprocessing pipeline used before OCR."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


class ImageProcessor:
    """Preprocess receipt images for better OCR quality."""

    def preprocess_for_ocr(self, image_path: str) -> str:
        """Run the complete image preprocessing pipeline and save output."""
        image = self.load_image(image_path)
        image = self.convert_to_grayscale(image)
        image = self.remove_noise(image)
        image = self.improve_contrast(image)
        image = self.threshold_image(image)
        image = self.deskew_image(image)
        image = self.sharpen_text(image)
        image = self.resize_if_needed(image)

        receipt_id = self._extract_receipt_id(image_path)
        output_dir = Path("/tmp")
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            output_dir = Path(tempfile.gettempdir())
        output_path = output_dir / f"folio_{receipt_id}_processed.png"
        cv2.imwrite(str(output_path), image)
        return str(output_path)

    def load_image(self, path: str):
        image_path = Path(path)
        suffix = image_path.suffix.lower()

        if suffix == ".heic":
            with Image.open(image_path) as pil_image:
                rgb_image = pil_image.convert("RGB")
                image = cv2.cvtColor(np.array(rgb_image), cv2.COLOR_RGB2BGR)
        else:
            image = cv2.imread(str(image_path))

        if image is None:
            raise ValueError("Unable to load image for preprocessing.")

        height, width = image.shape[:2]
        if width <= 100 or height <= 100:
            raise ValueError("Image dimensions are too small for OCR.")

        return image

    def convert_to_grayscale(self, image):
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def remove_noise(self, image):
        return cv2.fastNlMeansDenoising(
            image,
            h=10,
            searchWindowSize=21,
            templateWindowSize=7,
        )

    def improve_contrast(self, image):
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(image)

    def threshold_image(self, image):
        return cv2.adaptiveThreshold(
            image,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2,
        )

    def deskew_image(self, image):
        points = np.column_stack(np.where(image > 0))
        if len(points) < 10:
            return image

        angle = cv2.minAreaRect(points)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) <= 0.5:
            return image

        height, width = image.shape[:2]
        center = (width // 2, height // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            image,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    def sharpen_text(self, image):
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        return cv2.filter2D(image, -1, kernel)

    def resize_if_needed(self, image):
        height, width = image.shape[:2]
        if width < 1000:
            target_width = 1500
        elif width > 3000:
            target_width = 2500
        else:
            return image

        scale = target_width / float(width)
        target_height = max(1, int(height * scale))
        return cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_CUBIC)

    def get_image_quality_score(self, image) -> dict:
        if image is None:
            return {
                "brightness": 0.0,
                "contrast": 0.0,
                "sharpness": 0.0,
                "overall": 0.0,
                "warnings": ["Image is not available"],
            }

        if len(image.shape) == 3:
            grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            grayscale = image

        brightness = float(np.clip((np.mean(grayscale) / 255.0) * 100.0, 0, 100))
        contrast = float(np.clip((np.std(grayscale) / 64.0) * 100.0, 0, 100))
        sharp_raw = cv2.Laplacian(grayscale, cv2.CV_64F).var()
        sharpness = float(np.clip((sharp_raw / 1000.0) * 100.0, 0, 100))
        overall = round((brightness + contrast + sharpness) / 3.0, 2)

        warnings: list[str] = []
        if brightness < 25:
            warnings.append("Image appears too dark")
        elif brightness > 85:
            warnings.append("Image appears overexposed")
        if contrast < 20:
            warnings.append("Image has low contrast")
        if sharpness < 20:
            warnings.append("Image appears blurry")

        return {
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "sharpness": round(sharpness, 2),
            "overall": overall,
            "warnings": warnings,
        }

    def _extract_receipt_id(self, image_path: str) -> str:
        match = re.search(r"folio_([a-zA-Z0-9-]+)_", Path(image_path).name)
        if match:
            return match.group(1)
        return Path(image_path).stem.replace(".", "_")
