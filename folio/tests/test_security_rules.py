from pathlib import Path


def _firestore_rules() -> str:
    return Path("firestore.rules").read_text(encoding="utf-8")


def _storage_rules() -> str:
    return Path("storage.rules").read_text(encoding="utf-8")


def test_employee_cannot_read_other_users_receipt():
    rules = _firestore_rules()
    assert "match /receipts/{receiptId}" in rules
    assert "allow read: if isOwner(resource.data.userId) || isAdmin();" in rules


def test_employee_cannot_change_own_role():
    rules = _firestore_rules()
    assert "match /users/{userId}" in rules
    assert "request.resource.data.role == resource.data.role" in rules
    assert ".changedKeys()" in rules
    assert ".hasOnly(['firstName', 'surname', 'language'])" in rules


def test_admin_can_read_all_receipts():
    rules = _firestore_rules()
    assert "match /receipts/{receiptId}" in rules
    assert "allow read: if isOwner(resource.data.userId) || isAdmin();" in rules


def test_combined_documents_not_writable_by_clients():
    rules = _firestore_rules()
    assert "match /combined_documents/{docId}" in rules
    assert "allow create, update, delete: if false;" in rules


def test_audit_logs_not_readable_by_employees():
    rules = _firestore_rules()
    assert "match /audit_logs/{logId}" in rules
    assert "allow read: if isAdmin();" in rules


def test_storage_blocks_non_image_uploads():
    rules = _storage_rules()
    assert "request.resource.contentType.matches('image/.*')" in rules
    assert "request.resource.size < 15 * 1024 * 1024" in rules
