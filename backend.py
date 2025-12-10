from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os

app = Flask(__name__)

# CORS only for your site
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://bluemarble.consulting",
            "https://www.bluemarble.consulting"
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Simple health check
@app.route("/", methods=["GET"])
def index():
    return "BlueMarble AI Ebook backend is running.", 200


# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Storage directory
UPLOAD_DIR = "./storage"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------- STEP 1: UPLOAD ----------

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    file.save(filepath)

    # Return the full path so the frontend can reuse it directly
    return jsonify({"status": "success", "path": filepath}), 200


# ---------- STEP 2: TRANSCRIBE ----------

@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    data = request.get_json() or {}

    # Accept either "path" or "filename"
    raw_path = data.get("path") or data.get("filename")

    if not raw_path:
        return jsonify({"error": "No path or filename provided"}), 400

    # If the client already sent ./storage/xxx or /something, use it as-is.
    # Otherwise, assume it's just the bare filename.
    if os.path.isabs(raw_path) or raw_path.startswith("storage") or raw_path.startswith("./storage"):
        filepath = os.path.normpath(raw_path)
    else:
        filepath = os.path.join(UPLOAD_DIR, raw_path)

    if not os.path.exists(filepath):
        return jsonify({
            "error": "File not found",
            "filepath": filepath
        }), 400

    try:
        with open(filepath, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="gpt-4o-transcribe",  # your chosen model
                file=audio_file
            )
        return jsonify({"status": "success", "transcript": transcript.text}), 200

    except Exception as e:
        # If OpenAI or anything else fails, surface the message
        return jsonify({
            "error": "Transcription failed",
            "details": str(e)
        }), 500


# ---------- STEP 3: OUTLINE ----------

@app.route("/api/generate-outline", methods=["POST"])
def generate_outline():
    data = request.get_json() or {}
    text = data.get("text", "")

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "Create a professional ebook outline."},
                {"role": "user", "content": text}
            ]
        )

        outline = response.choices[0].message.content
        return jsonify({"outline": outline}), 200

    except Exception as e:
        return jsonify({
            "error": "Outline generation failed",
            "details": str(e)
        }), 500


# ---------- STEP 4: CHAPTER ----------

@app.route("/api/generate-chapter", methods=["POST"])
def generate_chapter():
    data = request.get_json() or {}
    outline = data.get("outline", "")

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "Write a detailed ebook chapter."},
                {"role": "user", "content": outline}
            ]
        )

        chapter = response.choices[0].message.content
        return jsonify({"chapter": chapter}), 200

    except Exception as e:
        return jsonify({
            "error": "Chapter generation failed",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    # For local debugging only; Render uses gunicorn
    app.run(host="0.0.0.0", port=5000)
