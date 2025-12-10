import os
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from app.config import Config

upload_bp = Blueprint("upload_bp", __name__)

ALLOWED_EXTENSIONS = {"mp3", "wav", "m4a"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@upload_bp.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"status": "error", "message": "Filename is empty"}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "status": "error",
            "message": "Unsupported file type. Allowed: mp3, wav, m4a"
        }), 400

    filename = secure_filename(file.filename)
    save_path = os.path.join(Config.LOCAL_STORAGE, filename)

    os.makedirs(Config.LOCAL_STORAGE, exist_ok=True)
    file.save(save_path)

    return jsonify({
        "status": "success",
        "message": "File uploaded successfully",
        "path": save_path
    }), 200
