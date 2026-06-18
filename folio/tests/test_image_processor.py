import tempfile
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
Image = pytest.importorskip("PIL.Image")

from utils.image_processor import ImageProcessor


def _write_image(array: np.ndarray, suffix: str = ".png") -> str:
    file_path = Path(tempfile.gettempdir()) / f"folio_test_image{suffix}"
    cv2.imwrite(str(file_path), array)
    return str(file_path)


def test_grayscale_conversion():
    processor = ImageProcessor()
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    gray = processor.convert_to_grayscale(image)
    assert len(gray.shape) == 2
    assert gray.shape == (120, 160)


def test_noise_removal_reduces_noise():
    processor = ImageProcessor()
    base = np.full((220, 220), 120, dtype=np.uint8)
    noise = np.random.randint(0, 40, (220, 220), dtype=np.uint8)
    noisy = cv2.add(base, noise)
    denoised = processor.remove_noise(noisy)
    assert float(np.std(denoised)) < float(np.std(noisy))


def test_deskew_corrects_rotated_image():
    processor = ImageProcessor()
    image = np.ones((400, 400), dtype=np.uint8) * 255
    cv2.putText(image, "RECEIPT", (40, 210), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 5)
    matrix = cv2.getRotationMatrix2D((200, 200), 30, 1.0)
    rotated = cv2.warpAffine(image, matrix, (400, 400), flags=cv2.INTER_CUBIC)

    corrected = processor.deskew_image(rotated)

    angle_before = cv2.minAreaRect(np.column_stack(np.where(rotated < 250)))[-1]
    angle_after = cv2.minAreaRect(np.column_stack(np.where(corrected < 250)))[-1]
    assert abs(angle_after) <= abs(angle_before)


def test_very_dark_image_returns_low_score():
    processor = ImageProcessor()
    dark_image = np.zeros((300, 300), dtype=np.uint8)
    score = processor.get_image_quality_score(dark_image)
    assert score["brightness"] < 25
    assert any("dark" in warning.lower() for warning in score["warnings"])


def test_preprocessing_improves_quality_score():
    processor = ImageProcessor()
    image = np.full((280, 420, 3), 70, dtype=np.uint8)
    cv2.putText(image, "Receipt Total 145.00", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 2)
    input_path = _write_image(image)

    loaded = processor.load_image(input_path)
    baseline_score = processor.get_image_quality_score(processor.convert_to_grayscale(loaded))
    output_path = processor.preprocess_for_ocr(input_path)
    processed = cv2.imread(output_path, cv2.IMREAD_GRAYSCALE)
    improved_score = processor.get_image_quality_score(processed)

    assert Path(output_path).exists()
    assert improved_score["overall"] >= baseline_score["overall"]


def test_pipeline_handles_heic_format():
    processor = ImageProcessor()
    image = Image.fromarray(np.full((180, 180, 3), 200, dtype=np.uint8))
    heic_path = Path(tempfile.gettempdir()) / "folio_heic_test.heic"
    image.save(heic_path, format="PNG")

    # We intentionally keep .heic extension to exercise conversion branch.
    loaded = processor.load_image(str(heic_path))
    assert loaded is not None
    assert loaded.shape[0] > 100 and loaded.shape[1] > 100
