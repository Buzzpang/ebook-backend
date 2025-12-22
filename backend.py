import os
import json
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

from openai import OpenAI

# Postgres (Neon)
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

# --- Storage directory (uploads) --------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Database (Neon Postgres) ------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def get_db():
    """
    Open a new Postgres connection.
    Neon typically requires sslmode=require (include it in DATABASE_URL).
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in Render Environment variables.")

    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
    )
    return conn


def init_db():
    """
    Create tables if they don't exist (Postgres).
    """
    conn = get_db()
    cur = conn.cursor()

    # Book projects
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
            outline_json JSONB,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        """
    )

    # Source documents
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS source_documents (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES book_projects(id) ON DELETE CASCADE,
            label TEXT,
            content_text TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        """
    )

    # Chapters
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES book_projects(id) ON DELETE CASCADE,
            chapter_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            draft_text TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        """
    )

    conn.commit()
    cur.close()
    conn.close()


def now_utc():
    return datetime.utcnow()


def row_to_dict(row):
    # RealDictCursor already returns dict rows
    return row


# Initialize DB at startup
try:
    init_db()
except Exception as e:
    # Don't crash the import in case Render starts before env var is set
    print(f"[WARN] init_db failed (will break DB endpoints until fixed): {e}")


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
            max_tokens=1200,
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
            max_tokens=1600,
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

    now = now_utc()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO book_projects
            (title, subtitle, target_audience, tone, language,
             word_count_target, outline_json, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (title, subtitle, target_audience, tone, language, word_count_target, None, now, now),
    )
    project = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "project": project}), 201


@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM book_projects ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "projects": rows}), 200


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def get_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute("SELECT COUNT(*) AS cnt FROM source_documents WHERE project_id = %s", (project_id,))
    project["source_document_count"] = cur.fetchone()["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE project_id = %s", (project_id,))
    project["chapter_count"] = cur.fetchone()["cnt"]

    cur.close()
    conn.close()
    return jsonify({"status": "success", "project": project}), 200


@app.route("/api/projects/<int:project_id>/add-text", methods=["POST"])
def add_text_source(project_id):
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text is required"}), 400

    label = (data.get("label") or "").strip() or "Untitled source"
    now = now_utc()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        INSERT INTO source_documents
            (project_id, label, content_text, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (project_id, label, text, now, now),
    )
    doc = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "source_document": doc}), 201


@app.route("/api/projects/<int:project_id>/sources", methods=["GET"])
def list_sources(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT * FROM source_documents
        WHERE project_id = %s
        ORDER BY created_at ASC
        """,
        (project_id,),
    )
    docs = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "source_documents": docs}), 200


@app.route("/api/projects/<int:project_id>/build-outline", methods=["POST"])
def build_outline_for_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT content_text FROM source_documents
        WHERE project_id = %s
        ORDER BY created_at ASC
        """,
        (project_id,),
    )
    source_rows = cur.fetchall()
    if not source_rows:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "No source documents found for project"}), 400

    full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()
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
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
        )
        outline_json_str = resp.choices[0].message.content
        outline_data = json.loads(outline_json_str)
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": f"Outline generation failed: {e}"}), 500

    chapters = outline_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Model did not return a valid 'chapters' list in JSON."}), 500

    now = now_utc()

    # Clear existing chapters for this project
    cur.execute("DELETE FROM chapters WHERE project_id = %s", (project_id,))

    saved_chapters = []
    for ch in chapters:
        order = int(ch.get("order") or 0)
        title = (ch.get("title") or "").strip()
        summary = (ch.get("summary") or "").strip() or None
        if not title:
            continue

        cur.execute(
            """
            INSERT INTO chapters
                (project_id, chapter_order, title, summary, draft_text, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (project_id, order, title, summary, None, now, now),
        )
        saved_chapters.append(cur.fetchone())

    # Persist outline JSON on project
    cur.execute(
        "UPDATE book_projects SET outline_json = %s, updated_at = %s WHERE id = %s",
        (json.dumps(outline_data), now, project_id),
    )

    conn.commit()
    cur.close()
    conn.close()

    saved_chapters.sort(key=lambda c: c["chapter_order"])
    return jsonify({"status": "success", "outline": outline_data, "chapters": saved_chapters}), 200


@app.route("/api/projects/<int:project_id>/chapters", methods=["GET"])
def list_chapters_for_project(project_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = %s
        ORDER BY chapter_order ASC
        """,
        (project_id,),
    )
    chapters = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "chapters": chapters}), 200


@app.route("/api/projects/<int:project_id>/chapters/<int:chapter_id>", methods=["GET"])
def get_project_chapter(project_id, chapter_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = %s AND id = %s
        """,
        (project_id, chapter_id),
    )
    chapter = cur.fetchone()
    cur.close()
    conn.close()

    if not chapter:
        return jsonify({"status": "error", "error": "Chapter not found"}), 404

    return jsonify({"status": "success", "chapter": chapter}), 200


@app.route("/api/chapters/<int:chapter_id>/generate-draft", methods=["POST"])
def generate_chapter_draft(chapter_id):
    conn = get_db()
    cur = conn.cursor()

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
    chapter = cur.fetchone()
    if not chapter:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Chapter not found"}), 404

    cur.execute(
        """
        SELECT content_text
        FROM source_documents
        WHERE project_id = %s
        ORDER BY created_at ASC
        """,
        (chapter["project_id"],),
    )
    sources = cur.fetchall()
    full_text = "\n\n".join(s["content_text"] for s in sources if s.get("content_text"))
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
            max_tokens=1600,
        )
        draft_text = resp.choices[0].message.content
    except Exception as e:
        cur.close()
        conn.close()
        print(f"Error generating draft for chapter {chapter_id}: {e}")
        return jsonify({"status": "error", "error": "Failed to generate chapter draft", "details": str(e)}), 500

    now = now_utc()
    cur.execute(
        """
        UPDATE chapters
        SET draft_text = %s, updated_at = %s
        WHERE id = %s
        """,
        (draft_text, now, chapter_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "chapter_id": chapter_id, "updated_at": now.isoformat() + "Z"}), 200


@app.route("/api/projects/<int:project_id>/generate-chapters", methods=["POST"])
def generate_chapters_for_project(project_id):
    """
    Generate ONE chapter draft per call – the first chapter that does not yet have draft_text.
    Call repeatedly until all chapters are filled.
    """
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "Project not found"}), 404

    cur.execute(
        """
        SELECT content_text FROM source_documents
        WHERE project_id = %s
        ORDER BY created_at ASC
        """,
        (project_id,),
    )
    source_rows = cur.fetchall()
    if not source_rows:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "No source documents found for project"}), 400

    full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()
    MAX_SOURCE_CHARS = 4000
    limited_text = full_text[:MAX_SOURCE_CHARS]

    cur.execute(
        """
        SELECT * FROM chapters
        WHERE project_id = %s
        ORDER BY chapter_order ASC
        """,
        (project_id,),
    )
    chapters = cur.fetchall()
    if not chapters:
        cur.close()
        conn.close()
        return jsonify({"status": "error", "error": "No chapters found for project"}), 400

    target = None
    for ch in chapters:
        if not ch.get("draft_text"):
            target = ch
            break

    if target is None:
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "All chapters already have drafts."}), 200

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
        f"Chapter {target['chapter_order']}: {target['title']}\n"
        f"Chapter summary: {target.get('summary') or 'No summary provided.'}\n\n"
        "Source material from the author (notes, transcripts, etc.):\n"
        f"{limited_text}\n\n"
        "Write a complete, well-structured chapter based on the chapter title, "
        "summary, and source material. Make it coherent, readable, and grounded "
        "in the source material where possible.\n"
        "- 800–1,200 words.\n"
        "- Use short paragraphs and helpful subheadings.\n"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1600,
        )
        draft_text = resp.choices[0].message.content
    except Exception as e:
        draft_text = f"[ERROR generating chapter: {e}]"

    now = now_utc()
    cur.execute(
        """
        UPDATE chapters
        SET draft_text = %s, updated_at = %s
        WHERE id = %s
        """,
        (draft_text, now, target["id"]),
    )
    conn.commit()

    # Return updated target
    target["draft_text"] = draft_text
    target["updated_at"] = now.isoformat() + "Z"

    cur.close()
    conn.close()
    return jsonify({"status": "success", "generated_chapters": [target]}), 200


# --- Local dev entrypoint (Render uses: gunicorn backend:app) ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

