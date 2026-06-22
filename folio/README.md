# Folio - Hospitality Expense & Receipt Automation Platform

Folio is a production-ready Flask web application scaffold for receipt ingestion,
OCR extraction, AI analysis, document storage, and hospitality expense workflows.

## Stack
- Flask backend
- PostgreSQL-backed document store (primary database)
- Session auth stored in PostgreSQL
- Local file storage for receipts and generated PDFs
- Tesseract OCR + OpenCV preprocessing
- OpenAI GPT-5.4 integration
- ReportLab PDF generation
- Resend email delivery
- HTML/CSS/Vanilla JS frontend

## Quickstart
1. Create a virtual environment and activate it.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment variables:
   ```bash
   cp .env.example .env
   ```
4. Configure required values in `.env`:
   - `FLASK_SECRET_KEY`
   - `APP_URL`
   - `DATABASE_URL` (PostgreSQL)
   - `STORAGE_ROOT` (local directory for uploaded files)
   - `OPENAI_API_KEY`
   - `RESEND_API_KEY`
   - `RESEND_FROM_EMAIL`
5. Ensure PostgreSQL is running and database exists.
6. Run development server:
   ```bash
   flask --app app:create_app run
   ```

## Project Structure
The repository is organized into clear layers:
- `routes/`: Flask blueprints
- `services/`: Integrations (OCR, AI, PDF, email, storage)
- `repositories/`: data access layer (SQL-backed document store)
- `middleware/`: auth and rate limiting
- `models/`: domain entities
- `utils/`: shared helpers

## Tests
Run:
```bash
pytest
```

## PostgreSQL Setup (pgAdmin)
1. Open pgAdmin and connect to your PostgreSQL server.
2. Create a database (for example `folio`).
3. Open Query Tool on that database and run `scripts/postgres_schema.sql`.
4. Refresh `Schemas -> public -> Tables` to see:
   - `users`
   - `receipts`
   - `forms`
   - `combined_documents`
   - `audit_logs`
5. Run `scripts/postgres_smoke_test.sql` to execute a real write/read transaction.

### Create tables through the app
For local SQLite development, run these from the project root:

```bash
flask --app app:create_app db-init
flask --app app:create_app db-smoke
```

For PostgreSQL, set `DATABASE_URL` first. The `db-init` command will then use
`scripts/postgres_schema.sql` instead of the local SQLite schema.

Expected output:
- `db-init` -> schema applied successfully
- `db-smoke` -> shows a created/read user row and audit row
