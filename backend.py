import os
import json
import sqlite3
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

# Optional Postgres (Neon) support
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor

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

# --- SQLite fallback (local only) -------------------------------------------

SQLITE_DB_PATH = os.path.join(BASE_DIR, "ebook.db")


def now_iso():
    """UTC timestamp in ISO format with Z."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ----------------------------------------------------------------------------
# DB Helpers (Postgres preferred via DATABASE_URL; SQLite fallback)
# ----------------------------------------------------------------------------

def get_db():
    """
    Returns a connection:
    - Postgres if DATABASE_URL is set
    - otherwise SQLite (local/dev fallback)
    """
    if USE_POSTGRES:
        # Neon typically requires SSL; your DATABASE_URL already includes sslmode=require.
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def fetchone(cur):
    row = cur.fetchone()
    # sqlite returns Row; postgres returns dict (RealDictCursor)
    if row is None:
        return None
    if USE_POSTGRES:
        return row  # already dict
    return {k: row[k] for k in row.keys()}


def fetchall(cur):
    rows = cur.fetchall()
    if USE_POSTGRES:
        return rows  # list of dicts
    return [{k: r[k] for k in r.keys()} for r in rows]


def init_db():
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        # Postgres schema
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS book_projects (
                id SERIAL PRIMARY KEY,
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

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS source_documents (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES book_projects(id) ON DELETE CASCADE,
                label TEXT,
                content_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chapters (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES book_projects(id) ON DELETE CASCADE,
                chapter_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                draft_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
    else:
        # SQLite schema
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

# --- Simple health check -----------------------------------------------------


@app.route("/")
def index():
    return "BlueMarble AI Ebook backend is running.", 200


# ============================================================================
#  EXISTING MVP ENDPOINTS (upload + transcribe + simple outline/chapter)
# ============================================================================

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"status": "error", "error": "Empty filename"}), 400

    filename = file.filename
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)

    rel_path = os.path.relpath(save_path, BASE_DIR)
    return jsonify({"status": "success", "path": rel_path}), 200


@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    data = request.get_json(silent=True) or {}
    path = data.get("path")

    if not path:
        return jsonify({"status": "error", "error": "Missing 'path' in body"}), 400

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


@app.route("/api/generate-outline", methods=["POST"])
def generate_outline():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

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
                {"role": "system", "content": "You help structure ebooks into clear outlines."},
                {"role": "user", "content": prompt},
            ],
        )
        outline = response.choices[0].message.content
        return jsonify({"status": "success", "outline": outline}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/generate-chapter", methods=["POST"])
def generate_chapter():
    data = request.get_json(silent=True) or {}
    outline = (data.get("outline") or "").strip()

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
                {"role": "system", "content": "You write detailed, well-structured ebook chapters."},
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

@app.route("/api/projects", methods=["POST"])
def create_project():
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

    if USE_POSTGRES:
        cur.execute(
            """
            INSERT INTO book_projects
                (title, subtitle, target_audience, tone, language,
                 word_count_target, outline_json, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (title, subtitle, target_audience, tone, language, word_count_target, None, created_at, updated_at),
        )
        project_id = cur.fetchone()["id"]
    else:
        cur.execute(
            """
            INSERT INTO book_projects
                (title, subtitle, target_audience, tone, language,
                 word_count_target, outline_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, subtitle, target_audience, tone, language, word_count_target, None, created_at, updated_at),
        )
        project_id = cur.lastrowid

    conn.commit()

    # fetch created row
    if USE_POSTGRES:
        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    else:
        cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    row = fetchone(cur)

    conn.close()
    return jsonify({"status": "success", "project": row}), 201


@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("SELECT * FROM book_projects ORDER BY created_at DESC")
    else:
        cur.execute("SELECT * FROM book_projects ORDER BY created_at DESC")

    projects = fetchall(cur)
    conn.close()
    return jsonify({"status": "success", "projects": projects}), 200


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def get_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    else:
        cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    project = fetchone(cur)

    if not project:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    if USE_POSTGRES:
        cur.execute("SELECT COUNT(*) AS cnt FROM source_documents WHERE project_id = %s", (project_id,))
        project["source_document_count"] = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE project_id = %s", (project_id,))
        project["chapter_count"] = cur.fetchone()["cnt"]
    else:
        cur.execute("SELECT COUNT(*) AS cnt FROM source_documents WHERE project_id = ?", (project_id,))
        project["source_document_count"] = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE project_id = ?", (project_id,))
        project["chapter_count"] = cur.fetchone()["cnt"]

    conn.close()
    return jsonify({"status": "success", "project": project}), 200


@app.route("/api/projects/<int:project_id>/add-text", methods=["POST"])
def add_text_source(project_id):
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text is required"}), 400

    label = (data.get("label") or "").strip() or "Untitled source"
    now = now_iso()

    conn = get_db()
    cur = conn.cursor()

    # ensure project exists
    if USE_POSTGRES:
        cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
    else:
        cur.execute("SELECT id FROM book_projects WHERE id = ?", (project_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    if USE_POSTGRES:
        cur.execute(
            """
            INSERT INTO source_documents (project_id, label, content_text, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (project_id, label, text, now, now),
        )
        doc_id = cur.fetchone()["id"]
        conn.commit()
        cur.execute("SELECT * FROM source_documents WHERE id = %s", (doc_id,))
    else:
        cur.execute(
            """
            INSERT INTO source_documents (project_id, label, content_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, label, text, now, now),
        )
        doc_id = cur.lastrowid
        conn.commit()
        cur.execute("SELECT * FROM source_documents WHERE id = ?", (doc_id,))

    row = fetchone(cur)
    conn.close()
    return jsonify({"status": "success", "source_document": row}), 201


@app.route("/api/projects/<int:project_id>/build-outline", methods=["POST"])
def build_outline_for_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    # ensure project exists
    if USE_POSTGRES:
        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    else:
        cur.execute("SELECT * FROM book_projects WHERE id = ?", (project_id,))
    project = fetchone(cur)

    if not project:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    # gather all source documents
    if USE_POSTGRES:
        cur.execute(
            "SELECT content_text FROM source_documents WHERE project_id = %s ORDER BY created_at ASC",
            (project_id,),
        )
    else:
        cur.execute(
            "SELECT content_text FROM source_documents WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        )

    source_rows = cur.fetchall()
    if not source_rows:
        conn.close()
        return jsonify({"status": "error", "error": "No source documents found for project"}), 400

    # source_rows items differ between sqlite/pg fetch style
    if USE_POSTGRES:
        full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()
    else:
        full_text = "\n\n".join(r[0] for r in source_rows).strip()

    MAX_SOURCE_CHARS = 4000
    limited_text = full_text[:MAX_SOURCE_CHARS]

    system_msg = "You are an expert editorial planner. You structure ebooks into clear chapters."

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
        + limited_text
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=900,
        )
        outline_json_str = response.choices[0].message.content
        outline_data = json.loads(outline_json_str)
    except Exception as e:
        conn.close()
        return jsonify({"status": "error", "error": f"Outline generation failed: {e}"}), 500

    chapters = outline_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        conn.close()
        return jsonify({"status": "error", "error": "Model did not return a valid 'chapters' list in JSON."}), 500

    now = now_iso()

    # Clear any existing chapters for this project
    if USE_POSTGRES:
        cur.execute("DELETE FROM chapters WHERE project_id = %s", (project_id,))
    else:
        cur.execute("DELETE FROM chapters WHERE project_id = ?", (project_id,))

    saved_chapters = []
    for ch in chapters:
        order = int(ch.get("order") or 0)
        title = (ch.get("title") or "").strip()
        summary = (ch.get("summary") or "").strip() or None
        if not title:
            continue

        if USE_POSTGRES:
            cur.execute(
                """
                INSERT INTO chapters
                    (project_id, chapter_order, title, summary, draft_text, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (project_id, order, title, summary, None, now, now),
            )
            chapter_id = cur.fetchone()["id"]
        else:
            cur.execute(
                """
                INSERT INTO chapters
                    (project_id, chapter_order, title, summary, draft_text, created_at, updated_at)
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
    if USE_POSTGRES:
        cur.execute(
            "UPDATE book_projects SET outline_json = %s, updated_at = %s WHERE id = %s",
            (json.dumps(outline_data), now, project_id),
        )
    else:
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


@app.route("/api/projects/<int:project_id>/chapters", methods=["GET"])
def list_chapters_for_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
    else:
        cur.execute("SELECT id FROM book_projects WHERE id = ?", (project_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    if USE_POSTGRES:
        cur.execute("SELECT * FROM chapters WHERE project_id = %s ORDER BY chapter_order ASC", (project_id,))
    else:
        cur.execute("SELECT * FROM chapters WHERE project_id = ? ORDER BY chapter_order ASC", (project_id,))

    chapters = fetchall(cur)
    conn.close()
    return jsonify({"status": "success", "chapters": chapters}), 200


@app.route("/api/chapters/<int:chapter_id>/generate-draft", methods=["POST"])
def generate_chapter_draft(chapter_id):
    conn = get_db()
    cur = conn.cursor()

    # Load chapter + its project info
    if USE_POSTGRES:
        cur.execute(
            """
            SELECT
                c.*,
                p.title AS project_title,
                p.subtitle AS project_subtitle,
                p.target_audience,
                p.tone,
                p.language
            FROM chapters c
            JOIN book_projects p ON c.project_id = p.id
            WHERE c.id = %s
            """,
            (chapter_id,),
        )
    else:
        cur.execute(
            """
            SELECT
                c.*,
                p.title AS project_title,
                p.subtitle AS project_subtitle,
                p.target_audience,
                p.tone,
                p.language
            FROM chapters c
            JOIN book_projects p ON c.project_id = p.id
            WHERE c.id = ?
            """,
            (chapter_id,),
        )

    chapter = fetchone(cur)
    if not chapter:
        conn.close()
        return jsonify({"status": "error", "error": "Chapter not found"}), 404

    # Load all source text for this project
    if USE_POSTGRES:
        cur.execute(
            "SELECT content_text FROM source_documents WHERE project_id = %s ORDER BY created_at ASC",
            (chapter["project_id"],),
        )
        source_rows = cur.fetchall()
        full_text = "\n\n".join(r["content_text"] for r in source_rows if r.get("content_text"))
    else:
        cur.execute(
            "SELECT content_text FROM source_documents WHERE project_id = ? ORDER BY created_at ASC",
            (chapter["project_id"],),
        )
        source_rows = cur.fetchall()
        full_text = "\n\n".join(r[0] for r in source_rows if r[0])

    MAX_SOURCE_CHARS = 4000
    limited_text = (full_text or "")[:MAX_SOURCE_CHARS]

    system_msg = (
        "You are a professional ghostwriter who writes clear, structured, "
        "business ebooks for busy professionals."
    )

    user_prompt = (
        f"You are writing a chapter for an ebook.\n\n"
        f"Book title: {chapter.get('project_title')}\n"
        f"Subtitle: {chapter.get('project_subtitle') or ''}\n"
        f"Target audience: {chapter.get('target_audience') or 'Business readers'}\n"
        f"Tone: {chapter.get('tone') or 'Professional'}\n"
        f"Language: {chapter.get('language') or 'en'}\n\n"
        f"Chapter {chapter['chapter_order']}: {chapter['title']}\n"
        f"Chapter summary:\n{chapter.get('summary') or 'No summary provided.'}\n\n"
        "Source material from the author (notes, transcripts, etc.):\n"
        f"{limited_text}\n\n"
        "Write a complete, well-structured draft of this chapter.\n"
        "- 800–1,200 words.\n"
        "- Use short paragraphs and helpful subheadings.\n"
        "- Keep the tone business-professional and easy to read.\n"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
        )
        draft_text = resp.choices[0].message.content
    except Exception as e:
        conn.close()
        return jsonify({"status": "error", "error": "Failed to generate chapter draft", "details": str(e)}), 500

    now = now_iso()

    # Save draft
    if USE_POSTGRES:
        cur.execute(
            "UPDATE chapters SET draft_text = %s, updated_at = %s WHERE id = %s",
            (draft_text, now, chapter_id),
        )
    else:
        cur.execute(
            "UPDATE chapters SET draft_text = ?, updated_at = ? WHERE id = ?",
            (draft_text, now, chapter_id),
        )

    conn.commit()
    conn.close()

    return jsonify(
        {
            "status": "success",
            "chapter_id": chapter_id,
            "updated_at": now,
            "draft_text": draft_text,
        }
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
