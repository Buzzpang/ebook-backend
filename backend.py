import os
import json
import sqlite3
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

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

# --- Storage directory & DB path --------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DB_PATH = os.path.join(BASE_DIR, "ebook.db")


def get_db():
    """Open a new SQLite connection with row dict access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    cur = conn.cursor()

    # Book projects
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS book_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subtitle TEXT,
            target_audience TEXT,
            tone TEXT,
            language TEXT,
            word_count_target INTEGER,
            outline_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    # Source documents (normalized text per project)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS source_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            label TEXT,
            content_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES book_projects(id)
        );
        """
    )

    # Chapters
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            chapter_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            draft_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES book_projects(id)
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


# --- Simple health check -----------------------------------------------------


@app.route("/")
def index():
    return "BlueMarble AI Ebook backend is running.", 200


# ============================================================================
#  EXISTING MVP ENDPOINTS (kept as-is)
# ============================================================================


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

    # Relative path used by frontend & /api/transcribe, e.g. "storage/foo.m4a"
    rel_path = os.path.relpath(save_path, BASE_DIR)

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
        return jsonify({"status": "error", "error": f"File not found at {path}"}), 400

    try:
        with open(audio_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=audio_file,
            )
        transcript_text = getattr(result, "text", None) or str(result)
        return jsonify({"status": "success", "transcript": transcript_text}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# --- STEP 3: Generate outline (legacy, free-form) ----------------------------


@app.route("/api/generate-outline", methods=["POST"])
def generate_outline():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"status": "error", "error": "No transcription text provided"}), 400

    prompt = (
        "You are an expert editor. Based on the following transcript, "
        "create a clear, professional ebook outline with parts, chapters, "
        "and bullet points where helpful.\n\nTRANSCRIPT:\n"
        + text
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


# --- STEP 4: Generate chapter (legacy, single chapter) -----------------------


@app.route("/api/generate-chapter", methods=["POST"])
def generate_chapter():
    data = request.get_json(silent=True) or {}
    outline = data.get("outline", "").strip()

    if not outline:
        return jsonify({"status": "error", "error": "No outline text provided"}), 400

    prompt = (
        "Using the following ebook outline, write the first full chapter. "
        "Use a clear, engaging, business-professional tone.\n\nOUTLINE:\n"
        + outline
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


# ============================================================================
#  NEW: PROJECTS + TEXT → OUTLINE (JSON) → MULTIPLE CHAPTER DRAFTS
# ============================================================================

# --- Helper to get timestamps ------------------------------------------------


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# --- PROJECTS ----------------------------------------------------------------


@app.route("/api/projects", methods=["POST"])
def create_project():
    """Create a new book project with basic configuration."""
    data = request.get_json(silent=True) or {}

    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"status": "error", "error": "title is required"}), 400

    subtitle = (data.get("subtitle") or "").strip() or None
    target_audience = (data.get("target_audience") or "").strip() or None
    tone = (data.get("tone") or "").strip() or None
    language = (data.get("language") or "en").strip()
    word_count_target = data.get("word_count_target")

    created_at = updated_at = now_iso()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO book_projects
            (title, subtitle, target_audience, tone, language,
             word_count_target, outline_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            subtitle,
            target_audience,
            tone,
            language,
            word_count_target,
            None,
            created_at,
            updated_at,
        ),
    )
    project_id = cur.lastrowid
    conn.commit()

    cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()

    return jsonify({"status": "success", "project": row_to_dict(row)}), 201


@app.route("/api/projects", methods=["GET"])
def list_projects():
    """List all projects (for later UI selection)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM book_projects ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()

    projects = [row_to_dict(r) for r in rows]
    return jsonify({"status": "success", "projects": projects}), 200


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def get_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    project = row_to_dict(row)

    # also count docs and chapters for quick overview
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM source_documents WHERE project_id = ?",
        (project_id,),
    )
    project["source_document_count"] = cur.fetchone()["cnt"]

    cur.execute(
        "SELECT COUNT(*) AS cnt FROM chapters WHERE project_id = ?",
        (project_id,),
    )
    project["chapter_count"] = cur.fetchone()["cnt"]

    conn.close()
    return jsonify({"status": "success", "project": project}), 200


# --- SOURCE DOCUMENTS (TEXT INPUT) ------------------------------------------


@app.route("/api/projects/<int:project_id>/add-text", methods=["POST"])
def add_text_source(project_id):
    """Attach a block of text (transcript, notes, etc.) to a project."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"status": "error", "error": "text is required"}), 400

    label = (data.get("label") or "").strip() or "Untitled source"
    now = now_iso()

    conn = get_db()
    cur = conn.cursor()

    # ensure project exists
    cur.execute("SELECT id FROM book_projects WHERE id = ?", (project_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        INSERT INTO source_documents
            (project_id, label, content_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, label, text, now, now),
    )
    doc_id = cur.lastrowid
    conn.commit()

    cur.execute("SELECT * FROM source_documents WHERE id = ?", (doc_id,))
    row = cur.fetchone()
    conn.close()

    return jsonify({"status": "success", "source_document": row_to_dict(row)}), 201


@app.route("/api/projects/<int:project_id>/sources", methods=["GET"])
def list_sources(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM book_projects WHERE id = ?", (project_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT * FROM source_documents
        WHERE project_id = ?
        ORDER BY created_at ASC
        """,
        (project_id,),
    )
    rows = cur.fetchall()
    conn.close()

    docs = [row_to_dict(r) for r in rows]
    return jsonify({"status": "success", "source_documents": docs}), 200


# --- OUTLINE (JSON) + CHAPTER CREATION --------------------------------------


@app.route("/api/projects/<int:project_id>/build-outline", methods=["POST"])
def build_outline_for_project(project_id):
    """Use all source text to build a JSON outline and create chapter rows."""
    conn = get_db()
    cur = conn.cursor()

    # ensure project exists
    cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    project_row = cur.fetchone()
    if project_row is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    project = row_to_dict(project_row)

    # gather all source documents
    cur.execute(
        "SELECT content_text FROM source_documents WHERE project_id = ? ORDER BY created_at ASC",
        (project_id,),
    )
    source_rows = cur.fetchall()

    if not source_rows:
        conn.close()
        return jsonify(
            {"status": "error", "error": "No source documents found for project"}
        ), 400

    full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()

    # Build JSON outline
    system_msg = (
        "You are an expert editorial planner. "
        "You structure ebooks into clear chapters."
    )

    user_prompt = (
        "Create a structured JSON outline for an ebook based on the provided material.\n"
        "The JSON must use this exact schema:\n\n"
        "{\n"
        '  "chapters": [\n'
        "    {\n"
        '      "order": 1,\n'
        '      "title": "Chapter title",\n'
        '      "summary": "2-3 sentence summary of the chapter"\n'
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "Constraints:\n"
        f"- Target audience: {project.get('target_audience') or 'Not specified'}\n"
        f"- Tone: {project.get('tone') or 'Business-professional'}\n"
        f"- Target language: {project.get('language') or 'en'}\n"
        "Make the number of chapters and structure appropriate for a serious ebook.\n\n"
        "SOURCE MATERIAL:\n"
        + full_text
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
        )
        outline_json_str = response.choices[0].message.content
        outline_data = json.loads(outline_json_str)
    except Exception as e:
        conn.close()
        return jsonify({"status": "error", "error": f"Outline generation failed: {e}"}), 500

    chapters = outline_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        conn.close()
        return jsonify(
            {
                "status": "error",
                "error": "Model did not return a valid 'chapters' list in JSON.",
            }
        ), 500

    now = now_iso()

    # Clear any existing chapters for this project (fresh outline)
    cur.execute("DELETE FROM chapters WHERE project_id = ?", (project_id,))

    # Insert new chapters
    saved_chapters = []
    for ch in chapters:
        order = int(ch.get("order") or 0)
        title = (ch.get("title") or "").strip()
        summary = (ch.get("summary") or "").strip() or None

        if not title:
            # Skip malformed entries
            continue

        cur.execute(
            """
            INSERT INTO chapters
                (project_id, chapter_order, title, summary, draft_text,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, order, title, summary, None, now, now),
        )
        chapter_id = cur.lastrowid
        saved_chapters.append(
            {
                "id": chapter_id,
                "project_id": project_id,
                "chapter_order": order,
                "title": title,
                "summary": summary,
                "draft_text": None,
                "created_at": now,
                "updated_at": now,
            }
        )

    # Persist outline JSON on project
    cur.execute(
        "UPDATE book_projects SET outline_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(outline_data), now, project_id),
    )

    conn.commit()
    conn.close()

    return jsonify(
        {
            "status": "success",
            "outline": outline_data,
            "chapters": sorted(saved_chapters, key=lambda c: c["chapter_order"]),
        }
    ), 200


# --- CHAPTER LIST & DETAIL ---------------------------------------------------


@app.route("/api/projects/<int:project_id>/chapters", methods=["GET"])
def list_chapters(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM book_projects WHERE id = ?", (project_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = ?
        ORDER BY chapter_order ASC
        """,
        (project_id,),
    )
    rows = cur.fetchall()
    conn.close()

    chapters = [row_to_dict(r) for r in rows]
    return jsonify({"status": "success", "chapters": chapters}), 200


@app.route(
    "/api/projects/<int:project_id>/chapters/<int:chapter_id>", methods=["GET"]
)
def get_chapter(project_id, chapter_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = ? AND id = ?
        """,
        (project_id, chapter_id),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"status": "error", "error": "Chapter not found"}), 404

    return jsonify({"status": "success", "chapter": row_to_dict(row)}), 200


# --- GENERATE DRAFTS FOR CHAPTERS -------------------------------------------


@app.route("/api/projects/<int:project_id>/generate-chapters", methods=["POST"])
def generate_chapters_for_project(project_id):
    """
    Generate AI drafts for all chapters that don't yet have draft_text.
    Uses all source text + per-chapter title & summary.
    """
    conn = get_db()
    cur = conn.cursor()

    # fetch project
    cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    project_row = cur.fetchone()
    if project_row is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    project = row_to_dict(project_row)

    # fetch combined source text
    cur.execute(
        "SELECT content_text FROM source_documents WHERE project_id = ? ORDER BY created_at ASC",
        (project_id,),
    )
    source_rows = cur.fetchall()
    if not source_rows:
        conn.close()
        return jsonify(
            {"status": "error", "error": "No source documents found for project"}
        ), 400

    full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()

    # fetch chapters needing drafts
    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = ? AND (draft_text IS NULL OR draft_text = '')
        ORDER BY chapter_order ASC
        """,
        (project_id,),
    )
    chapter_rows = cur.fetchall()

    if not chapter_rows:
        conn.close()
        return jsonify(
            {"status": "success", "message": "All chapters already have drafts."}
        ), 200

    generated = []
    now = now_iso()

    for row in chapter_rows:
        chap = row_to_dict(row)

        system_msg = (
            "You are a professional ghostwriter who creates structured, "
            "book-quality chapters for business and memoir-style ebooks."
        )

        user_prompt = (
            f"You are writing a chapter for an ebook.\n\n"
            f"Project title: {project.get('title')}\n"
            f"Subtitle: {project.get('subtitle') or ''}\n"
            f"Target audience: {project.get('target_audience') or 'Not specified'}\n"
            f"Tone: {project.get('tone') or 'Business-professional'}\n"
            f"Language: {project.get('language') or 'en'}\n\n"
            f"Chapter {chap['chapter_order']}: {chap['title']}\n"
            f"Chapter summary: {chap.get('summary') or 'No summary provided.'}\n\n"
            "Source material from the author (notes, transcripts, etc.):\n"
            f"{full_text}\n\n"
            "Write a complete, well-structured chapter based on the chapter title, "
            "summary, and source material. Make it coherent, readable, and grounded "
            "in the source material where possible."
        )

        try:
            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt},
                ],
            )
            draft_text = resp.choices[0].message.content
        except Exception as e:
            # If one chapter fails, note it and continue
            draft_text = f"[ERROR generating chapter: {e}]"

        cur.execute(
            """
            UPDATE chapters
            SET draft_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (draft_text, now, chap["id"]),
        )

        chap["draft_text"] = draft_text
        chap["updated_at"] = now
        generated.append(chap)

    conn.commit()
    conn.close()

    # return all chapters (including newly generated ones) for convenience
    generated_sorted = sorted(generated, key=lambda c: c["chapter_order"])

    return jsonify(
        {
            "status": "success",
            "generated_chapters": generated_sorted,
        }
    ), 200


# --- Local dev entrypoint (Render uses gunicorn backend:app) -----------------


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
