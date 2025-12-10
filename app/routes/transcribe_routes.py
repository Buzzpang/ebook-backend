# app/routes/transcribe_routes.py

from flask import Blueprint, request, jsonify
from app.services.whisper_service import transcribe_audio
import os

transcribe_bp = Blueprint("transcribe_bp", __name__)

@transcribe_bp.route("/transcribe", methods=["POST"])
def transcribe():
    data = request.get_json()
    filename = data.get("filename")

    if not filename:
        return jsonify({"error": "filename is required"}), 400

    file_path = os.path.join("./storage", filename)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found", "path": file_path}), 404

    try:
        transcript = transcribe_audio(file_path)
        return jsonify({
            "status": "success",
            "filename": filename,
            "transcript": transcript
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
