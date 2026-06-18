from flask import Blueprint, jsonify

from middleware.auth_middleware import require_auth

api_bp = Blueprint("api", __name__)


@api_bp.get("/status")
@require_auth
def api_status():
    return jsonify({"module": "api", "status": "ok"})
