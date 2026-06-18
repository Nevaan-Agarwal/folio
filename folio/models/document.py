"""Document models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CombinedDocumentModel:
    id: str
    formId: str
    receiptId: str
    userId: str
    filePath: str
    downloadUrl: str
    createdAt: datetime | None = None
    emailSent: bool = False
    emailSentAt: str | None = None
    emailMessageId: str | None = None
    emailDeliveryStatus: str = "pending"
    userEmail: str = ""
