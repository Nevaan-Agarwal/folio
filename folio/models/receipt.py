"""Receipt model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ReceiptModel:
    id: str
    userId: str
    imageUrl: str
    uploadedAt: datetime | None = None
    ocrText: str = ""
    ocrConfidence: float | None = None
    merchant: str = ""
    address: str = ""
    date: str = ""
    currency: str = ""
    subtotal: float | None = None
    tax: float | None = None
    tip: float | None = None
    total: float | None = None
    receiptNumber: str = ""
    processingStatus: str = "uploaded"
    reviewStatus: str = "draft"
    errorMessage: str = ""
