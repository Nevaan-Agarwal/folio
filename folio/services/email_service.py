"""Email delivery service using Resend."""

from __future__ import annotations

import base64
import json
import os
from urllib import error, request as urllib_request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import database as database_config
from repositories import audit_repository, combined_document_repository


class EmailService:
    def __init__(self):
        template_root = Path(__file__).resolve().parents[1] / "templates"
        self._jinja = Environment(
            loader=FileSystemLoader(str(template_root)),
            autoescape=select_autoescape(["html"]),
        )

    def _sanitize_filename(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in value)

    def _config_value(self, key: str, default: str = "") -> str:
        if has_app_context():
            configured = current_app.config.get(key)
            if configured is not None:
                return str(configured)
        return os.getenv(key, default)

    def _normalize_api_key(self, raw_key: str) -> str:
        key = (raw_key or "").strip().strip('"').strip("'")
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        return key

    def _resolve_resend_config(self) -> tuple[str | None, str | None, str | None]:
        api_key = self._normalize_api_key(self._config_value("RESEND_API_KEY", ""))
        from_email = self._config_value("RESEND_FROM_EMAIL", "").strip()
        if not api_key:
            return None, None, "Resend authentication failed: RESEND_API_KEY is missing."
        if not api_key.startswith("re_"):
            return None, None, (
                "Resend authentication failed: RESEND_API_KEY looks invalid. "
                "Use the full key from Resend (usually starts with re_)."
            )
        if not from_email:
            from_email = "Folio <onboarding@resend.dev>"
        return api_key, from_email, None

    def _is_sender_restriction_error(self, message: str | None) -> bool:
        lowered = (message or "").lower()
        sender_markers = (
            "invalid `from`",
            "from address",
            "sender",
            "domain is not verified",
            "not verified",
        )
        return any(marker in lowered for marker in sender_markers)

    def _perform_resend_request(self, api_key: str, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        req = urllib_request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
                decoded = json.loads(body or "{}")
                return decoded.get("id"), None
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            return None, f"Resend error: {exc.code} {body}"
        except Exception as exc:
            return None, str(exc)

    def _render_inline_template(self, template: str, context: dict[str, Any]) -> str:
        return self._jinja.from_string(template).render(**context).strip()

    def _template_context(
        self,
        user_name: str,
        form_data: dict,
        pdf_download_url: str,
        document_id: str,
    ) -> dict[str, Any]:
        merchant = form_data.get("merchant") or "-"
        date = form_data.get("date") or form_data.get("dateOfHospitality") or "-"
        category = form_data.get("expenseCategory") or "-"
        total_value = float(form_data.get("totalAmount") or 0)
        total_amount = f"EUR {total_value:.2f}"
        employee_name = user_name or "User"
        return {
            "employee_name": employee_name,
            "user_name": employee_name,  # backwards-compatible alias
            "document_id": document_id,
            "document_merchant": merchant,
            "document_date": date,
            "document_category": category,
            "document_total": total_amount,
            "pdf_download_url": pdf_download_url,
            "support_email": self._config_value("SUPPORT_EMAIL", "support@folio.app"),
        }

    def _build_subject(self, form_data: dict, context: dict[str, Any]) -> str:
        merchant = form_data.get("merchant") or "Unknown Merchant"
        date = form_data.get("date") or form_data.get("dateOfHospitality") or "-"
        lang = form_data.get("language", "en")
        custom_subject_template = self._config_value("FOLIO_EMAIL_SUBJECT_TEMPLATE", "").strip()
        if custom_subject_template:
            try:
                return self._render_inline_template(custom_subject_template, context)
            except Exception:
                pass
        if lang == "de":
            return f"Ihr Folio Spesenbericht — {merchant} {date}"
        return f"Your Folio Expense Report — {merchant} {date}"

    def _build_plain_text(self, context: dict[str, Any]) -> str:
        custom_text_template = self._config_value("FOLIO_EMAIL_TEXT_TEMPLATE", "").strip()
        if custom_text_template:
            try:
                return self._render_inline_template(custom_text_template, context)
            except Exception:
                pass
        return (
            f"Hello {context['employee_name']},\n\n"
            "Your Folio expense report is ready.\n"
            f"Merchant: {context['document_merchant']}\n"
            f"Date: {context['document_date']}\n"
            f"Category: {context['document_category']}\n"
            f"Total Amount: {context['document_total']}\n\n"
            f"Download PDF: {context['pdf_download_url']}\n\n"
            "This expense report was automatically generated by Folio.\n"
            f"Support: {context['support_email']}\n"
        )

    def _build_html(self, context: dict[str, Any]) -> str:
        template = self._jinja.get_template("emails/pdf_delivery.html")
        return template.render(
            **context,
            intro_line=self._config_value(
                "FOLIO_EMAIL_INTRO_TEMPLATE",
                "Your expense report is ready. A PDF copy is attached and can also be downloaded below.",
            ),
            cta_label=self._config_value("FOLIO_EMAIL_CTA_LABEL", "Download PDF"),
            footer_note=self._config_value(
                "FOLIO_EMAIL_FOOTER_TEMPLATE",
                "This expense report was automatically generated by Folio.",
            ),
        )

    def _send_via_resend(
        self,
        to_email: str,
        subject: str,
        plain_text: str,
        html_content: str,
        pdf_bytes: bytes,
        filename: str,
    ) -> tuple[str | None, str | None]:
        api_key, from_email, config_error = self._resolve_resend_config()
        if config_error:
            return None, config_error

        payload = {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": plain_text,
            "html": html_content,
            "attachments": [
                {
                    "filename": filename,
                    "content": base64.b64encode(pdf_bytes).decode("utf-8"),
                }
            ],
        }
        message_id, send_error = self._perform_resend_request(api_key=api_key, payload=payload)
        if message_id is not None:
            return message_id, None

        # Fallback for common local misconfiguration: unverified custom sender.
        # Keep user's configured sender as primary, but degrade gracefully.
        fallback_sender = "Folio <onboarding@resend.dev>"
        lowered_error = (send_error or "").lower()
        should_try_fallback_sender = self._is_sender_restriction_error(send_error) or "resend error: 403" in lowered_error
        if should_try_fallback_sender and from_email != fallback_sender:
            payload["from"] = fallback_sender
            fallback_id, fallback_error = self._perform_resend_request(api_key=api_key, payload=payload)
            if fallback_id is not None:
                return fallback_id, None
            return None, fallback_error

        return None, send_error

    def _humanize_delivery_error(self, error_message: str | None) -> str:
        message = (error_message or "").strip()
        lowered = message.lower()
        if "testing emails" in lowered or "sandbox" in lowered:
            return (
                "Resend sandbox restriction: you can only send to verified addresses. "
                "Use your own verified recipient or verify a domain/address in Resend."
            )
        if "invalid `from`" in lowered or "from address" in lowered:
            return (
                "Sender address is not allowed by Resend. "
                "Set RESEND_FROM_EMAIL to a verified sender/domain."
            )
        if "resend authentication failed:" in lowered:
            return message
        if self._is_sender_restriction_error(message):
            return (
                "Sender address is not allowed by Resend. "
                "Use a verified sender or keep RESEND_FROM_EMAIL empty to use onboarding@resend.dev."
            )
        if "api key" in lowered or "unauthorized" in lowered:
            return "Resend authentication failed. Check RESEND_API_KEY."
        return message or "Unknown delivery error."

    def send_pdf_delivery(
        self,
        to_email: str,
        user_name: str,
        form_data: dict,
        pdf_download_url: str,
        pdf_bytes: bytes,
        document_id: str,
    ) -> dict[str, Any]:
        context = self._template_context(
            user_name=user_name,
            form_data=form_data,
            pdf_download_url=pdf_download_url,
            document_id=document_id,
        )
        subject = self._build_subject(form_data, context)
        plain_text = self._build_plain_text(context)
        html_content = self._build_html(context)
        filename = self._sanitize_filename(
            f"Folio_Expense_{form_data.get('merchant', 'merchant')}_{form_data.get('date') or form_data.get('dateOfHospitality') or 'date'}.pdf"
        )

        database_config.db.collection("combined_documents").document(document_id).set(
            {"emailDeliveryStatus": "pending", "emailError": None},
            merge=True,
        )
        try:
            message_id, error = self._send_via_resend(
                to_email=to_email,
                subject=subject,
                plain_text=plain_text,
                html_content=html_content,
                pdf_bytes=pdf_bytes,
                filename=filename,
            )
            if error:
                friendly_error = self._humanize_delivery_error(error)
                database_config.db.collection("combined_documents").document(document_id).set(
                    {
                        "emailSent": True,
                        "emailSentAt": datetime.now(timezone.utc).isoformat(),
                        "emailMessageId": None,
                        "emailDeliveryStatus": "failed",
                        "emailError": friendly_error,
                    },
                    merge=True,
                )
                return {"success": False, "message_id": None, "error": friendly_error}

            combined_document_repository.update_email_status(document_id, "sent", message_id)
            payload = {}
            if database_config.db is not None:
                document = database_config.db.collection("combined_documents").document(document_id).get()
                payload = document.to_dict() if document.exists else {}
            audit_repository.create_log(
                user_id=payload.get("userId", ""),
                action="email_sent",
                details={
                    "documentId": document_id,
                    "email": to_email,
                    "messageId": message_id,
                },
            )
            return {"success": True, "message_id": message_id, "error": None}
        except Exception as exc:
            friendly_error = self._humanize_delivery_error(str(exc))
            database_config.db.collection("combined_documents").document(document_id).set(
                {
                    "emailSent": True,
                    "emailSentAt": datetime.now(timezone.utc).isoformat(),
                    "emailMessageId": None,
                    "emailDeliveryStatus": "failed",
                    "emailError": friendly_error,
                },
                merge=True,
            )
            return {"success": False, "message_id": None, "error": friendly_error}

    def send_email_with_html(
        self,
        api_key: str,
        from_email: str,
        to_email: str,
        subject: str,
        plain_text: str,
        html_content: str,
    ) -> dict[str, Any]:
        if not api_key or not from_email:
            return {"success": False, "message_id": None, "error": "Missing Resend configuration."}
        payload = {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": plain_text,
            "html": html_content,
        }
        req = urllib_request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
                decoded = json.loads(body or "{}")
                return {"success": True, "message_id": decoded.get("id"), "error": None}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            return {"success": False, "message_id": None, "error": f"Resend error: {exc.code} {body}"}
        except Exception as exc:
            return {"success": False, "message_id": None, "error": str(exc)}


email_service = EmailService()


def send_pdf_delivery(
    to_email: str,
    user_name: str,
    form_data: dict,
    pdf_download_url: str,
    pdf_bytes: bytes,
    document_id: str,
) -> dict[str, Any]:
    return email_service.send_pdf_delivery(
        to_email=to_email,
        user_name=user_name,
        form_data=form_data,
        pdf_download_url=pdf_download_url,
        pdf_bytes=pdf_bytes,
        document_id=document_id,
    )


def send_email_with_html(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    plain_text: str,
    html_content: str,
) -> dict[str, Any]:
    return email_service.send_email_with_html(
        api_key=api_key,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        plain_text=plain_text,
        html_content=html_content,
    )
