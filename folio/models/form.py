"""Form model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FormModel:
    id: str
    receiptId: str
    userId: str
    type: str
    expenseCategory: str
    host: str
    hostedPersons: list[str] = field(default_factory=list)
    occasion: str = ""
    dateOfHospitality: str | None = None
    locationOfHospitality: str = ""
    invoiceAmount: float | None = None
    tip: float | None = None
    totalAmount: float | None = None
    merchant: str = ""
    receiptNumber: str = ""
    date: str | None = None
    place: str = ""
    missingFields: list[str] = field(default_factory=list)
    needsManualReview: bool = False
    aiConfidence: dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
