from flask import Blueprint, jsonify

from middleware.auth_middleware import require_auth

pdf_bp = Blueprint("pdf", __name__)


@pdf_bp.get("/status")
@require_auth
def pdf_status():
    return jsonify({"module": "pdf", "status": "ok"})
