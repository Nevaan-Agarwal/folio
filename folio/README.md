# Folio - Hospitality Expense & Receipt Automation Platform

Folio is a production-ready Flask web application scaffold for receipt ingestion,
OCR extraction, AI analysis, document storage, and hospitality expense workflows.

## Stack
- Flask backend
- Firebase Authentication, Firestore, Storage
- Tesseract OCR + OpenCV preprocessing
- OpenAI GPT-5.4 integration
- ReportLab PDF generation
- SendGrid email delivery
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
4. Configure Firebase credentials and API keys in `.env`.
5. Run development server:
   ```bash
   flask --app app:create_app run
   ```

## Project Structure
The repository is organized into clear layers:
- `routes/`: Flask blueprints
- `services/`: Integrations (OCR, AI, PDF, email, storage)
- `repositories/`: Firestore access
- `middleware/`: auth and rate limiting
- `models/`: domain entities
- `utils/`: shared helpers

## Tests
Run:
```bash
pytest
```
