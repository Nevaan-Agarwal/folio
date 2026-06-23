import json

from prompts.ai_prompts import ALLOWED_EXPENSE_CATEGORIES
from services.ai_service import AiService


def _build_client(payload, usage=None, capture=None):
    if usage is None:
        usage = type(
            "Usage",
            (),
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )()

    class _CompletionAPI:
        @staticmethod
        def create(**kwargs):
            if capture is not None:
                capture["kwargs"] = kwargs
            content = json.dumps(payload)
            message = type("Message", (), {"content": content})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice], "usage": usage})()

    class _ChatAPI:
        completions = _CompletionAPI()

    class _Client:
        chat = _ChatAPI()

    return _Client()


def _valid_payload():
    return {
        "merchant": "Hotel Berlin",
        "address": "Alexanderplatz 1",
        "receiptNumber": "RB-123",
        "date": "2026-06-17",
        "currency": "EUR",
        "subtotal": 100.0,
        "tax": 19.0,
        "tip": 5.0,
        "total": 124.0,
        "expenseCategory": "Hotel",
        "tagDerBewirtung": "2026-06-17",
        "ortDerBewirtung": "Berlin",
        "anlasDerBewirtung": "Client dinner",
        "suggestedDescription": "Business dinner with client.",
        "language": "de",
        "confidence": {"overall": 0.9, "merchant": 0.9, "total": 0.92, "date": 0.88},
        "missingFields": [],
        "rawDataUsed": "Merchant, total, date found",
    }


def _setup_repo_mocks(monkeypatch):
    calls = {"status": [], "receipt": [], "form": [], "audit": []}
    monkeypatch.setattr(
        "services.ai_service.receipt_repository.update_processing_status",
        lambda receipt_id, status: calls["status"].append((receipt_id, status)),
    )
    monkeypatch.setattr(
        "services.ai_service.receipt_repository.update_receipt",
        lambda receipt_id, data: calls["receipt"].append((receipt_id, data)),
    )
    monkeypatch.setattr(
        "services.ai_service.form_repository.create_form_from_ai_result",
        lambda receipt_id, user_id, ai_result: calls["form"].append((receipt_id, user_id, ai_result)),
    )
    monkeypatch.setattr(
        "services.ai_service.audit_repository.log_event",
        lambda event: calls["audit"].append(event),
    )
    return calls


def test_ai_returns_valid_json(monkeypatch):
    calls = _setup_repo_mocks(monkeypatch)
    service = AiService(client=_build_client(_valid_payload()))
    result = service.process_receipt("r1", "merchant total date")
    assert isinstance(result, dict)
    assert result["expenseCategory"] in ALLOWED_EXPENSE_CATEGORIES
    assert calls["receipt"][0][1]["processingStatus"] == "awaiting_review"


def test_ai_returns_null_not_hallucination(monkeypatch):
    calls = _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["merchant"] = None
    payload["address"] = None
    service = AiService(client=_build_client(payload))
    result = service.process_receipt("r2", "total 50")
    assert result["merchant"] is None
    assert result["address"] is None
    assert "merchant" in result["missingFields"]
    assert calls["status"][0][1] == "ai_processing"


def test_category_always_in_allowed_list(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["expenseCategory"] = "InvalidCategory"
    service = AiService(client=_build_client(payload))
    result = service.process_receipt("r3", "some ocr text")
    assert result["expenseCategory"] in ALLOWED_EXPENSE_CATEGORIES


def test_amount_always_numeric_or_null(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["total"] = "123.45"
    payload["tax"] = "n/a"
    service = AiService(client=_build_client(payload))
    result = service.process_receipt("r4", "some ocr text")
    assert isinstance(result["total"], float)
    assert result["tax"] is None


def test_labeled_ocr_amounts_correct_ai_field_mismatch(monkeypatch):
    calls = _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["subtotal"] = None
    payload["tax"] = None
    payload["tip"] = None
    payload["total"] = 30.0
    service = AiService(client=_build_client(payload))

    result = service.process_receipt(
        "r4b",
        "\n".join(
            [
                "Merchant Cafe",
                "Subtotal 30.00",
                "Tax 5.00",
                "Tip 2.00",
                "Total 37.00",
            ]
        ),
    )

    assert result["subtotal"] == 30.0
    assert result["tax"] == 5.0
    assert result["tip"] == 2.0
    assert result["total"] == 37.0
    form_payload = calls["form"][0][2]
    assert form_payload["subtotal"] == 30.0
    assert form_payload["total"] == 37.0


def test_date_always_iso_format_or_null(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["date"] = "17/06/2026"
    service = AiService(client=_build_client(payload))
    result = service.process_receipt("r5", "some ocr text")
    assert result["date"] is None


def test_missing_fields_correctly_populated(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    payload = _valid_payload()
    payload["tax"] = None
    payload["tip"] = None
    service = AiService(client=_build_client(payload))
    result = service.process_receipt("r6", "some ocr text")
    assert "tax" in result["missingFields"]
    assert "tip" in result["missingFields"]


def test_ocr_text_truncated_at_4000_chars(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    capture = {}
    service = AiService(client=_build_client(_valid_payload(), capture=capture))
    long_ocr_text = "A" * 5000
    service.process_receipt("r7", long_ocr_text)
    user_prompt = capture["kwargs"]["messages"][1]["content"]
    assert user_prompt.count("A") == 4000
    assert "A" * 4001 not in user_prompt


def test_ai_never_receives_image_data(monkeypatch):
    _setup_repo_mocks(monkeypatch)
    capture = {}
    service = AiService(client=_build_client(_valid_payload(), capture=capture))
    suspicious_text = "https://storage.googleapis.com/bucket/receipt.png"
    service.process_receipt("r8", suspicious_text)
    messages = capture["kwargs"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "image" not in capture["kwargs"]


def test_empty_ocr_returns_graceful_error(monkeypatch):
    calls = _setup_repo_mocks(monkeypatch)
    service = AiService(client=_build_client(_valid_payload()))
    result = service.process_receipt("r9", "")
    assert "error" in result
    assert calls["status"][-1] == ("r9", "error")
