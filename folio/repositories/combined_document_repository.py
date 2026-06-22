"""Combined document repository wrappers."""

from __future__ import annotations

from models.document import CombinedDocumentModel
from repositories import document_repository


def update_email_status(doc_id: str, status: str, message_id: str | None) -> None:
    document_repository.update_email_status(doc_id, status, message_id)


def get_document(doc_id: str) -> CombinedDocumentModel | None:
    return document_repository.get_document(doc_id)


def delete_document(doc_id: str) -> None:
    document_repository.delete_document(doc_id)
