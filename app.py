from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os

app = Flask(__name__)
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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

UPLOAD_DIR = "./storage"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    file.save(filepath)

    return jsonify({"status": "success", "path": filepath})


@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    data = request.get_json()
    filename = data.get("filename")
    filepath = os.path.join(UPLOAD_DIR, filename)

    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 400

    with open(filepath, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file
        )

    return jsonify({"status": "success", "transcript": transcript.text})


@app.route("/api/generate-outline", methods=["POST"])
def generate_outline():
    data = request.get_json()
    text = data.get("text", "")

    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "Create a professional ebook outline."},
            {"role": "user", "content": text}
        ]
    )

    outline = response.choices[0].message.content
    return jsonify({"outline": outline})


@app.route("/api/generate-chapter", methods=["POST"])
def generate_chapter():
    data = request.get_json()
    outline = data.get("outline", "")

    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "Write a detailed ebook chapter."},
            {"role": "user", "content": outline}
        ]
    )

    chapter = response.choices[0].message.content
    return jsonify({"chapter": chapter})


if __name__ == "__main__":
    app.run()
# redeploy fix
