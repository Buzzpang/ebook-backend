from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from openai import OpenAI

client = OpenAI()

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "storage"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------------------------------
# 1. UPLOAD API
# ---------------------------------------
@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"})

    file = request.files["file"]
    filename = file.filename
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    return jsonify({"status": "success", "path": save_path})


# ---------------------------------------
# 2. TRANSCRIBE API
# ---------------------------------------
@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    data = request.json
    filename = data.get("filename")

    if not filename:
        return jsonify({"status": "error", "message": "Missing filename"})

    file_path = os.path.join(UPLOAD_FOLDER, filename)

    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "File not found on server"})

    # Whisper API
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file
        )

    return jsonify({"status": "success", "transcript": transcript.text})


# ---------------------------------------
# 3. OUTLINE API
# ---------------------------------------
@app.route("/api/generate-outline", methods=["POST"])
def gen_outline():
    data = request.json
    text = data.get("text", "")

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You create detailed book outlines."},
            {"role": "user", "content": text}
        ]
    )

    outline = completion.choices[0].message["content"]
    return jsonify({"outline": outline})


# ---------------------------------------
# 4. CHAPTER API
# ---------------------------------------
@app.route("/api/generate-chapter", methods=["POST"])
def gen_chapter():
    data = request.json
    outline = data.get("outline", "")

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You write book chapters from outlines."},
            {"role": "user", "content": outline}
        ]
    )

    chapter = completion.choices[0].message["content"]
    return jsonify({"chapter": chapter})


@app.route("/")
def home():
    return "BlueMarble Ebook Backend is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
