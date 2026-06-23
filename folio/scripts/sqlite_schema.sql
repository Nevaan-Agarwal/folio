-- Folio SQLite schema for local development and smoke tests.

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    surname TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee', 'admin')),
    language TEXT NOT NULL DEFAULT 'en',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    image_url TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ocr_text TEXT DEFAULT '',
    ocr_confidence REAL,
    merchant TEXT DEFAULT '',
    address TEXT DEFAULT '',
    receipt_date TEXT DEFAULT '',
    currency TEXT DEFAULT '',
    subtotal REAL,
    tax REAL,
    tip REAL,
    total REAL,
    receipt_number TEXT DEFAULT '',
    processing_status TEXT DEFAULT 'uploaded',
    review_status TEXT DEFAULT 'draft',
    error_message TEXT DEFAULT '',
    pdf_url TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS forms (
    id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    form_type TEXT DEFAULT 'Hospitality Expense',
    expense_category TEXT DEFAULT 'Other',
    host TEXT DEFAULT '',
    hosted_persons TEXT DEFAULT '[]',
    occasion TEXT DEFAULT '',
    date_of_hospitality TEXT,
    location_of_hospitality TEXT DEFAULT '',
    invoice_amount REAL,
    tip REAL,
    total_amount REAL,
    merchant TEXT DEFAULT '',
    receipt_number TEXT DEFAULT '',
    form_date TEXT,
    place TEXT DEFAULT '',
    missing_fields TEXT DEFAULT '[]',
    needs_manual_review INTEGER DEFAULT 0,
    ai_confidence TEXT DEFAULT '{}',
    status TEXT DEFAULT 'draft',
    rejection_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS combined_documents (
    id TEXT PRIMARY KEY,
    form_id TEXT NOT NULL REFERENCES forms(id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    file_path TEXT NOT NULL,
    download_url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    email_sent INTEGER NOT NULL DEFAULT 0,
    email_sent_at TEXT,
    email_message_id TEXT,
    email_delivery_status TEXT DEFAULT 'pending',
    email_error TEXT,
    user_email TEXT DEFAULT '',
    merchant TEXT DEFAULT '',
    category TEXT DEFAULT 'Other',
    host TEXT DEFAULT '',
    occasion TEXT DEFAULT '',
    total_amount REAL,
    currency TEXT DEFAULT 'EUR',
    status TEXT DEFAULT 'processing'
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    action TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    read_by TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS analytics_cache (
    id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_receipts_user_id ON receipts(user_id);
CREATE INDEX IF NOT EXISTS idx_forms_user_id ON forms(user_id);
CREATE INDEX IF NOT EXISTS idx_forms_receipt_id ON forms(receipt_id);
CREATE INDEX IF NOT EXISTS idx_combined_documents_user_id ON combined_documents(user_id);
CREATE INDEX IF NOT EXISTS idx_combined_documents_form_id ON combined_documents(form_id);
CREATE INDEX IF NOT EXISTS idx_combined_documents_receipt_id ON combined_documents(receipt_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC);
