-- Folio PostgreSQL schema based on current app models.
-- Run this inside your target PostgreSQL database (for example: folio).

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    surname TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee', 'admin')),
    language TEXT NOT NULL DEFAULT 'en',
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    image_url TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ocr_text TEXT NOT NULL DEFAULT '',
    ocr_confidence DOUBLE PRECISION,
    merchant TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    receipt_date TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT '',
    subtotal DOUBLE PRECISION,
    tax DOUBLE PRECISION,
    tip DOUBLE PRECISION,
    total DOUBLE PRECISION,
    receipt_number TEXT NOT NULL DEFAULT '',
    processing_status TEXT NOT NULL DEFAULT 'uploaded',
    review_status TEXT NOT NULL DEFAULT 'draft',
    error_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS forms (
    id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    form_type TEXT NOT NULL DEFAULT 'Hospitality Expense',
    expense_category TEXT NOT NULL DEFAULT 'Other',
    host TEXT NOT NULL DEFAULT '',
    hosted_persons JSONB NOT NULL DEFAULT '[]'::jsonb,
    occasion TEXT NOT NULL DEFAULT '',
    date_of_hospitality TEXT,
    location_of_hospitality TEXT NOT NULL DEFAULT '',
    invoice_amount DOUBLE PRECISION,
    tip DOUBLE PRECISION,
    total_amount DOUBLE PRECISION,
    merchant TEXT NOT NULL DEFAULT '',
    receipt_number TEXT NOT NULL DEFAULT '',
    form_date TEXT,
    place TEXT NOT NULL DEFAULT '',
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    needs_manual_review BOOLEAN NOT NULL DEFAULT FALSE,
    ai_confidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS combined_documents (
    id TEXT PRIMARY KEY,
    form_id TEXT NOT NULL REFERENCES forms(id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    file_path TEXT NOT NULL,
    download_url TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
    email_sent_at TIMESTAMPTZ,
    email_message_id TEXT,
    email_delivery_status TEXT NOT NULL DEFAULT 'pending',
    user_email TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT,
    action TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT ''
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
