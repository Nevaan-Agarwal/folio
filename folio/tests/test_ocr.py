from types import SimpleNamespace

from services import ocr_service as ocr_module


def _build_service(monkeypatch, words, confidences, text):
    service = ocr_module.OcrService()
    service.image_processor = SimpleNamespace(preprocess_for_ocr=lambda image_path: image_path)

    monkeypatch.setattr(
        ocr_module,
        "Output",
        SimpleNamespace(DICT="DICT"),
    )
    monkeypatch.setattr(
        ocr_module,
        "pytesseract",
        SimpleNamespace(
            image_to_data=lambda *args, **kwargs: {"text": words, "conf": confidences},
            image_to_string=lambda *args, **kwargs: text,
        ),
    )
    return service


def test_ocr_extracts_text_from_clear_receipt(monkeypatch):
    updates = {"status": [], "receipt": []}
    service = _build_service(
        monkeypatch,
        words=["Hotel", "Invoice", "Total"],
        confidences=["90", "88", "93"],
        text="Hotel Invoice Total 120.00",
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_processing_status",
        lambda receipt_id, status: updates["status"].append(status),
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_receipt",
        lambda receipt_id, data: updates["receipt"].append(data),
    )

    result = service.run_ocr("r1", "receipt.png")

    assert "Hotel Invoice" in result["raw_text"]
    assert result["is_readable"] is True
    assert updates["status"][0] == "ocr_processing"


def test_ocr_handles_german_receipt(monkeypatch):
    service = _build_service(
        monkeypatch,
        words=["Rechnung", "Betrag", "MwSt", "und", "für"],
        confidences=["80", "82", "79", "75", "77"],
        text="Rechnung Betrag MwSt und für",
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_processing_status",
        lambda receipt_id, status: None,
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_receipt",
        lambda receipt_id, data: None,
    )

    result = service.run_ocr("r2", "receipt.png")
    assert result["language_detected"] == "deu"


def test_confidence_score_between_0_and_100(monkeypatch):
    service = _build_service(
        monkeypatch,
        words=["a", "b", "c"],
        confidences=["10", "55", "95"],
        text="a b c",
    )
    monkeypatch.setattr(ocr_module.receipt_repository, "update_processing_status", lambda *_: None)
    monkeypatch.setattr(ocr_module.receipt_repository, "update_receipt", lambda *_: None)

    result = service.run_ocr("r3", "receipt.png")
    assert 0 <= result["confidence"] <= 100


def test_low_quality_image_sets_low_confidence(monkeypatch):
    service = _build_service(
        monkeypatch,
        words=["unclear", "text"],
        confidences=["20", "18"],
        text="unclear text",
    )
    monkeypatch.setattr(ocr_module.receipt_repository, "update_processing_status", lambda *_: None)
    monkeypatch.setattr(ocr_module.receipt_repository, "update_receipt", lambda *_: None)

    result = service.run_ocr("r4", "receipt.png")
    assert result["confidence"] < 60
    assert result["is_readable"] is False


def test_status_updates_to_ocr_complete(monkeypatch):
    status_updates = []
    receipt_updates = []
    service = _build_service(
        monkeypatch,
        words=["Total"],
        confidences=["85"],
        text="Total 50.00",
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_processing_status",
        lambda receipt_id, status: status_updates.append(status),
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_receipt",
        lambda receipt_id, data: receipt_updates.append(data),
    )

    service.run_ocr("r5", "receipt.png")
    assert status_updates[0] == "ocr_processing"
    assert receipt_updates[0]["processingStatus"] == "ocr_complete"


def test_ocr_failure_sets_error_status(monkeypatch):
    status_updates = []
    service = ocr_module.OcrService()
    service.image_processor = SimpleNamespace(
        preprocess_for_ocr=lambda _path: (_ for _ in ()).throw(RuntimeError("preprocess failed"))
    )
    monkeypatch.setattr(
        ocr_module.receipt_repository,
        "update_processing_status",
        lambda receipt_id, status: status_updates.append(status),
    )
    monkeypatch.setattr(
        ocr_module,
        "Output",
        SimpleNamespace(DICT="DICT"),
    )
    monkeypatch.setattr(
        ocr_module,
        "pytesseract",
        SimpleNamespace(
            image_to_data=lambda *args, **kwargs: {"text": [], "conf": []},
            image_to_string=lambda *args, **kwargs: "",
        ),
    )

    result = service.run_ocr("r6", "receipt.png")
    assert result["is_readable"] is False
    assert "error" in result
    assert status_updates[-1] == "error"
