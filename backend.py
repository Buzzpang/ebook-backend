from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os

# --- Flask & CORS setup ------------------------------------------------------

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "https://bluemarble.consulting",
                "https://www.bluemarble.consulting",
            ],
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
        }
    },
)

# --- OpenAI client -----------------------------------------------------------

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Storage directory -------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --- Simple health check -----------------------------------------------------

@app.route("/")
def index():
    return "BlueMarble AI Ebook backend is running.", 200


# --- STEP 1: Upload audio ----------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"status": "error", "error": "Empty filename"}), 400

    # In a real app you'd use secure_filename; for now we keep it simple.
    filename = file.filename
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)

    # Relative path used by frontend & /api/transcribe
    rel_path = os.path.relpath(save_path, BASE_DIR)  # e.g. "storage/foo.m4a"

    return jsonify({"status": "success", "path": rel_path}), 200


# --- STEP 2: Transcribe audio ------------------------------------------------

@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    data = request.get_json(silent=True) or {}
    path = data.get("path")

    if not path:
        return jsonify({"status": "error", "error": "Missing 'path' in body"}), 400

    # Normalize path: allow "storage/..." or "./storage/..."
    if path.startswith("./"):
        path = path[2:]

    audio_path = os.path.join(BASE_DIR, path)

    if not os.path.exists(audio_path):
        return jsonify(
            {"status": "error", "error": f"File not found at {path}"}
        ), 400

    try:
        with open(audio_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=audio_file,
            )

        # openai v1.x returns an object with .text
        transcript_text = getattr(result, "text", None) or str(result)

        return jsonify(
            {"status": "success", "transcript": transcript_text}
        ), 200

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# --- STEP 3: Generate outline ------------------------------------------------

@app.route("/api/generate-outline", methods=["POST"])
def generate_outline():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify(
            {"status": "error", "error": "No transcription text provided"}
        ), 400

    prompt = (
        "You are an expert editor. Based on the following transcript, "
        "create a clear, professional ebook outline with parts, chapters, "
        "and bullet points where helpful.\n\nTRANSCRIPT:\n" + text
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": "You help structure ebooks into clear outlines.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        outline = response.choices[0].message.content
        return jsonify({"status": "success", "outline": outline}), 200

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# --- STEP 4: Generate chapter -----------------------------------------------

@app.route("/api/generate-chapter", methods=["POST"])
def generate_chapter():
    data = request.get_json(silent=True) or {}
    outline = data.get("outline", "").strip()

    if not outline:
        return jsonify(
            {"status": "error", "error": "No outline text provided"}
        ), 400

    prompt = (
        "Using the following ebook outline, write the first full chapter. "
        "Use a clear, engaging, business-professional tone.\n\nOUTLINE:\n" + outline
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": "You write detailed, well-structured ebook chapters.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        chapter = response.choices[0].message.content
        return jsonify({"status": "success", "chapter": chapter}), 200

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# --- Local dev entrypoint (Render uses gunicorn backend:app) -----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
