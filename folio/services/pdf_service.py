"""ReportLab-backed PDF generation service for combined form + receipt output."""

from __future__ import annotations

import os
import shutil
import threading
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urlparse

from config import database as database_config
from repositories import audit_repository
from services import email_service

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        Image,
        PageBreak,
        HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor

    REPORTLAB_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - tested via importorskip in PDF tests
    REPORTLAB_AVAILABLE = False
    A4 = colors = mm = SimpleDocTemplate = Paragraph = Spacer = Table = TableStyle = Image = PageBreak = HRFlowable = None
    getSampleStyleSheet = ParagraphStyle = canvas = HexColor = None


class PdfService:
    NAVY = HexColor("#0D1240") if REPORTLAB_AVAILABLE else None
    SURFACE = HexColor("#124F94") if REPORTLAB_AVAILABLE else None
    AMBER = HexColor("#E07E00") if REPORTLAB_AVAILABLE else None
    TEXT_PRIMARY = HexColor("#F0EDE8") if REPORTLAB_AVAILABLE else None
    TEXT_SECONDARY = HexColor("#8A97B0") if REPORTLAB_AVAILABLE else None
    BORDER = HexColor("#002A69") if REPORTLAB_AVAILABLE else None
    ERROR = HexColor("#DD3D00") if REPORTLAB_AVAILABLE else None
    WHITE = HexColor("#FFFFFF") if REPORTLAB_AVAILABLE else None
    LIGHT_GRAY = HexColor("#F5F5F5") if REPORTLAB_AVAILABLE else None

    def __init__(self):
        self.styles = getSampleStyleSheet() if REPORTLAB_AVAILABLE else None
        if REPORTLAB_AVAILABLE:
            self._register_custom_styles()

    def _register_custom_styles(self) -> None:
        self.styles.add(
            ParagraphStyle(
                name="FolioSectionHeader",
                parent=self.styles["Heading4"],
                textColor=self.AMBER,
                backColor=self.SURFACE,
                fontName="Helvetica-Bold",
                fontSize=10,
                leading=12,
                spaceBefore=10,
                spaceAfter=8,
                leftIndent=6,
                rightIndent=6,
                uppercase=True,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="FolioLabel",
                parent=self.styles["BodyText"],
                textColor=self.TEXT_SECONDARY,
                fontSize=8,
                leading=11,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="FolioValue",
                parent=self.styles["BodyText"],
                textColor=self.NAVY,
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="FolioMuted",
                parent=self.styles["BodyText"],
                textColor=self.TEXT_SECONDARY,
                fontSize=8,
                leading=10,
            )
        )

    def _resolve_company_logo_path(self) -> str | None:
        configured = os.getenv("COMPANY_LOGO_PATH", "").strip()
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        candidates.append(
            Path(__file__).resolve().parent.parent / "static" / "images" / "company_logo.png"
        )
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue
        return None

    def _build_brand_cell(self):
        logo_path = self._resolve_company_logo_path()
        if logo_path:
            try:
                logo = Image(logo_path)
                logo.hAlign = "LEFT"
                logo._restrictSize(72 * mm, 18 * mm)
                return logo
            except Exception:
                pass
        return Paragraph("<b><font color='#E07E00'>Folio</font></b>", self.styles["Title"])

    def _fetch_form_data(self, form_id: str) -> dict:
        doc = database_config.db.collection("forms").document(form_id).get()
        if not doc.exists:
            raise ValueError("Form not found.")
        payload = doc.to_dict() or {}
        payload["id"] = doc.id
        return payload

    def _fetch_receipt_data(self, receipt_id: str) -> dict:
        doc = database_config.db.collection("receipts").document(receipt_id).get()
        if not doc.exists:
            raise ValueError("Receipt not found.")
        payload = doc.to_dict() or {}
        payload["id"] = doc.id
        return payload

    def _fetch_user_data(self, user_id: str) -> dict:
        doc = database_config.db.collection("users").document(user_id).get()
        if not doc.exists:
            return {"firstName": "", "surname": "", "email": ""}
        return doc.to_dict() or {}

    def _download_receipt_image(self, image_url: str) -> str:
        parsed = urlparse(image_url or "")
        if parsed.scheme == "file":
            decoded = unquote(parsed.path or "")
            if decoded.startswith("/") and len(decoded) > 2 and decoded[2] == ":":
                decoded = decoded[1:]
            source_path = decoded
            if not os.path.exists(source_path):
                raise ValueError("Receipt image file not found.")
            with NamedTemporaryFile(delete=False, suffix=os.path.splitext(source_path)[1] or ".jpg") as temp:
                with open(source_path, "rb") as src:
                    shutil.copyfileobj(src, temp)
                return temp.name

        with NamedTemporaryFile(delete=False, suffix=".jpg") as temp:
            with urllib.request.urlopen(image_url, timeout=10) as response:
                temp.write(response.read())
            return temp.name

    def _format_currency(self, value) -> str:
        amount = float(value or 0)
        return f"EUR {amount:,.2f}"

    def _build_page_1(self, form_data: dict, user_data: dict) -> list:
        story = []
        document_ref = (
            f"FOL-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(form_data.get('id', 'NA'))[:6].upper()}"
        )

        header_table = Table(
            [
                [
                    self._build_brand_cell(),
                    Paragraph(
                        "<para align='right'><font color='#FFFFFF'>Bewirtungsbeleg / Hospitality Expense Form</font></para>",
                        self.styles["BodyText"],
                    ),
                ]
            ],
            colWidths=[100 * mm, 80 * mm],
            rowHeights=[22 * mm],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), self.NAVY),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(header_table)
        story.append(HRFlowable(width="100%", color=self.AMBER, thickness=1))
        story.append(Spacer(1, 5))

        info_table = Table(
            [
                ["Document #", document_ref],
                ["Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ],
            colWidths=[30 * mm, 60 * mm],
            hAlign="RIGHT",
        )
        info_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), self.LIGHT_GRAY),
                    ("GRID", (0, 0), (-1, -1), 0.5, self.BORDER),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ]
            )
        )
        story.append(info_table)
        story.append(Spacer(1, 8))

        story.append(Paragraph("BASIC INFORMATION", self.styles["FolioSectionHeader"]))
        basic_rows = [
            [Paragraph("Type", self.styles["FolioLabel"]), Paragraph(str(form_data.get("type") or "-"), self.styles["FolioValue"])],
            [Paragraph("Category", self.styles["FolioLabel"]), Paragraph(str(form_data.get("expenseCategory") or "-"), self.styles["FolioValue"])],
            [Paragraph("Receipt Number", self.styles["FolioLabel"]), Paragraph(str(form_data.get("receiptNumber") or "-"), self.styles["FolioValue"])],
            [Paragraph("Merchant", self.styles["FolioLabel"]), Paragraph(str(form_data.get("merchant") or "-"), self.styles["FolioValue"])],
        ]
        basic_table = Table(basic_rows, colWidths=[55 * mm, 125 * mm])
        basic_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, self.BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(basic_table)
        story.append(Spacer(1, 8))

        story.append(
            Paragraph("BEWIRTUNGSDETAILS / HOSPITALITY DETAILS", self.styles["FolioSectionHeader"])
        )
        hosp_rows = [
            ["<b>Tag der Bewirtung</b> <i>/ Date</i>", form_data.get("dateOfHospitality") or "-"],
            ["<b>Ort der Bewirtung</b> <i>/ Location</i>", form_data.get("locationOfHospitality") or "-"],
            ["<b>Bewirtende Person</b> <i>/ Host</i>", form_data.get("host") or "-"],
            ["<b>Anlass der Bewirtung</b> <i>/ Occasion</i>", form_data.get("occasion") or "-"],
        ]
        hosp_table = Table(
            [[Paragraph(label, self.styles["BodyText"]), Paragraph(str(value), self.styles["FolioValue"])] for label, value in hosp_rows],
            colWidths=[70 * mm, 110 * mm],
        )
        hosp_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, self.BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(hosp_table)
        story.append(Spacer(1, 8))

        story.append(Paragraph("Bewirtete Personen / Guests", self.styles["FolioSectionHeader"]))
        guests = form_data.get("hostedPersons") or []
        if isinstance(guests, str):
            guests = [line.strip() for line in guests.splitlines() if line.strip()]
        guest_rows = [["#", "Name"]] + [[str(index), guest] for index, guest in enumerate(guests, start=1)]
        if len(guest_rows) == 1:
            guest_rows.append(["1", "-"])
        guests_table = Table(guest_rows, colWidths=[20 * mm, 160 * mm])
        guests_style = [
            ("GRID", (0, 0), (-1, -1), 0.4, self.BORDER),
            ("BACKGROUND", (0, 0), (-1, 0), self.SURFACE),
            ("TEXTCOLOR", (0, 0), (-1, 0), self.AMBER),
        ]
        for row_idx in range(1, len(guest_rows)):
            if row_idx % 2 == 0:
                guests_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), self.LIGHT_GRAY))
        guests_table.setStyle(TableStyle(guests_style))
        story.append(guests_table)
        story.append(Spacer(1, 8))

        story.append(Paragraph("FINANCIAL SUMMARY", self.styles["FolioSectionHeader"]))
        financial_rows = [
            ["Invoice Amount / Rechnungsbetrag", self._format_currency(form_data.get("invoiceAmount"))],
            ["Tip / Trinkgeld", self._format_currency(form_data.get("tip"))],
            ["TOTAL AMOUNT / GESAMTBETRAG", self._format_currency(form_data.get("totalAmount"))],
        ]
        fin_table = Table(financial_rows, colWidths=[120 * mm, 60 * mm])
        fin_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.6, self.BORDER),
                    ("BACKGROUND", (0, 2), (-1, 2), self.NAVY),
                    ("TEXTCOLOR", (0, 2), (-1, 2), self.AMBER),
                    ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 2), (-1, 2), 12),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ]
            )
        )
        story.append(fin_table)
        story.append(Spacer(1, 24))
        story.append(
            Paragraph(
                "Signature: _________________________   Date: _____________",
                self.styles["BodyText"],
            )
        )
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(
                f"Generated by Folio • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                self.styles["FolioMuted"],
            )
        )
        return story

    def _build_page_2(self, receipt_image_path: str, document_ref: str) -> list:
        story = [PageBreak()]
        story.append(
            Paragraph(
                "<para align='center'><b>ORIGINAL RECEIPT / ORIGINALER KASSENBON</b></para>",
                self.styles["Heading3"],
            )
        )
        story.append(HRFlowable(width="100%", color=self.AMBER, thickness=1.2))
        story.append(Spacer(1, 10))

        try:
            receipt_img = Image(receipt_image_path)
            receipt_img.hAlign = "CENTER"
            receipt_img._restrictSize(150 * mm, 220 * mm)
            story.append(receipt_img)
        except Exception:
            story.append(
                Paragraph(
                    "<para align='center'>Receipt image preview unavailable for this document.</para>",
                    self.styles["FolioMuted"],
                )
            )
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                "<para align='center'>Original receipt attached for verification purposes</para>",
                self.styles["FolioMuted"],
            )
        )
        story.append(Paragraph(f"<para align='center'>Reference: {document_ref}</para>", self.styles["FolioMuted"]))
        return story

    def _normalize_form_data_for_pdf(self, form_data: dict, receipt_data: dict) -> dict:
        normalized = dict(form_data or {})
        if normalized.get("merchant") in (None, ""):
            normalized["merchant"] = receipt_data.get("merchant") or "-"
        if normalized.get("receiptNumber") in (None, ""):
            normalized["receiptNumber"] = receipt_data.get("receiptNumber") or "-"
        if normalized.get("date") in (None, ""):
            normalized["date"] = receipt_data.get("date") or normalized.get("dateOfHospitality") or "-"
        if normalized.get("place") in (None, ""):
            normalized["place"] = receipt_data.get("address") or normalized.get("locationOfHospitality") or "-"
        if normalized.get("invoiceAmount") in (None, ""):
            normalized["invoiceAmount"] = receipt_data.get("subtotal") or 0
        if normalized.get("tip") in (None, ""):
            normalized["tip"] = receipt_data.get("tip") or 0
        if normalized.get("totalAmount") in (None, ""):
            normalized["totalAmount"] = receipt_data.get("total") or 0
        return normalized

    def _build_pdf_bytes(
        self,
        form_data: dict,
        receipt_data: dict,
        user_data: dict,
        receipt_image_path: str,
    ) -> bytes:
        if not REPORTLAB_AVAILABLE:
            plain = (
                f"Folio\nDocument: {form_data.get('id')}\nType: {form_data.get('type')}\n"
                f"Merchant: {form_data.get('merchant')}\nGuests: {form_data.get('hostedPersons')}\n"
                "ORIGINAL RECEIPT"
            )
            return plain.encode("utf-8")

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
            pageCompression=0,
        )
        story = []
        document_ref = (
            f"FOL-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(form_data.get('id', 'NA'))[:6].upper()}"
        )
        story.extend(self._build_page_1(form_data, user_data))
        story.extend(self._build_page_2(receipt_image_path, document_ref))
        doc.build(story)
        return buffer.getvalue()

    def _upload_pdf(self, user_id: str, document_id: str, pdf_bytes: bytes) -> tuple[str, str]:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        storage_path = f"combined_documents/{user_id}/{document_id}/folio_{date_str}.pdf"
        blob = database_config.bucket.blob(storage_path)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        try:
            url = blob.generate_signed_url(expiration=365 * 24 * 60 * 60)
        except Exception:
            url = blob.public_url
        return storage_path, url

    def _save_combined_document(
        self,
        document_id: str,
        form_id: str,
        receipt_id: str,
        user_id: str,
        file_path: str,
        download_url: str,
        user_email: str,
        form_data: dict,
        receipt_data: dict,
    ) -> None:
        payload = {
            "id": document_id,
            "formId": form_id,
            "receiptId": receipt_id,
            "userId": user_id,
            "filePath": file_path,
            "downloadUrl": download_url,
            "emailSent": False,
            "emailSentAt": None,
            "emailMessageId": None,
            "emailDeliveryStatus": "pending",
            "emailError": None,
            "userEmail": user_email or "",
            "merchant": form_data.get("merchant") or receipt_data.get("merchant") or "",
            "category": form_data.get("expenseCategory") or "Other",
            "host": form_data.get("host") or "",
            "occasion": form_data.get("occasion") or "",
            "totalAmount": float(form_data.get("totalAmount") or receipt_data.get("total") or 0),
            "currency": receipt_data.get("currency") or "EUR",
            "status": receipt_data.get("processingStatus") or "pdf_generated",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            doc_ref = database_config.db.collection("combined_documents").document(document_id)
            doc_ref.set(payload)
            saved = doc_ref.get()
            if not saved.exists:
                raise RuntimeError("Combined document was not persisted.")
            audit_repository.create_log(
                user_id=user_id,
                action="db_transaction",
                details={
                    "status": "success",
                    "operation": "upsert",
                    "collection": "combined_documents",
                    "docId": document_id,
                },
            )
        except Exception as exc:
            audit_repository.create_log(
                user_id=user_id,
                action="db_transaction",
                details={
                    "status": "failed",
                    "operation": "upsert",
                    "collection": "combined_documents",
                    "docId": document_id,
                    "error": str(exc),
                },
            )
            raise

    def _send_document_email(
        self,
        user_data: dict,
        form_data: dict,
        download_url: str,
        pdf_bytes: bytes,
        document_id: str,
    ) -> None:
        recipient = user_data.get("email")
        if not recipient:
            return
        email_service.send_pdf_delivery(
            to_email=recipient,
            user_name=f"{user_data.get('firstName', '')} {user_data.get('surname', '')}".strip() or "User",
            form_data=form_data,
            pdf_download_url=download_url,
            pdf_bytes=pdf_bytes,
            document_id=document_id,
        )

    def generate_pdf(self, form_id: str, receipt_id: str, user_id: str) -> str:
        form_data = self._fetch_form_data(form_id)
        receipt_data = self._fetch_receipt_data(receipt_id)
        user_data = self._fetch_user_data(user_id)
        form_data = self._normalize_form_data_for_pdf(form_data, receipt_data)

        receipt_image_path = self._download_receipt_image(receipt_data.get("imageUrl", ""))
        document_id = f"{form_id}-{receipt_id}"
        try:
            pdf_bytes = self._build_pdf_bytes(form_data, receipt_data, user_data, receipt_image_path)
            storage_path, download_url = self._upload_pdf(user_id, document_id, pdf_bytes)
            self._save_combined_document(
                document_id,
                form_id,
                receipt_id,
                user_id,
                storage_path,
                download_url,
                user_data.get("email", ""),
                form_data,
                receipt_data,
            )
            receipt_ref = database_config.db.collection("receipts").document(receipt_id)
            try:
                receipt_ref.set(
                    {"processingStatus": "pdf_generated", "pdfUrl": download_url},
                    merge=True,
                )
                updated_receipt = receipt_ref.get()
                if not updated_receipt.exists:
                    raise RuntimeError("Receipt update failed while marking PDF generated.")
                audit_repository.create_log(
                    user_id=user_id,
                    action="db_transaction",
                    details={
                        "status": "success",
                        "operation": "update",
                        "collection": "receipts",
                        "docId": receipt_id,
                    },
                )
            except Exception as exc:
                audit_repository.create_log(
                    user_id=user_id,
                    action="db_transaction",
                    details={
                        "status": "failed",
                        "operation": "update",
                        "collection": "receipts",
                        "docId": receipt_id,
                        "error": str(exc),
                    },
                )
                raise
            audit_repository.create_log(
                user_id=user_id,
                action="pdf_generated",
                details={
                    "receiptId": receipt_id,
                    "formId": form_id,
                    "documentId": document_id,
                },
            )
            threading.Thread(
                target=self._send_document_email,
                args=(user_data, form_data, download_url, pdf_bytes, document_id),
                daemon=True,
            ).start()
            return download_url
        finally:
            if receipt_image_path and os.path.exists(receipt_image_path):
                os.remove(receipt_image_path)


pdf_service = PdfService()


def generate_pdf(form_id: str, receipt_id: str, user_id: str) -> str:
    return pdf_service.generate_pdf(form_id, receipt_id, user_id)
