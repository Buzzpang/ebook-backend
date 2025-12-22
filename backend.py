import os
import sys
import json
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

import psycopg
from psycopg.rows import dict_row

from openai import OpenAI

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# Debug / deployment marker
# -----------------------------------------------------------------------------

DEBUG_MARKER = os.getenv("DEBUG_MARKER", "DEPLOY-CHECK-2025-12-23-D")  # change if needed


@app.route("/api/debug/whoami", methods=["GET"])
def whoami():
    return jsonify(
        {
            "status": "debug",
            "marker": DEBUG_MARKER,
            "python_version": sys.version.split()[0],
            "render_git_commit": os.getenv("RENDER_GIT_COMMIT", "unknown"),
            "render_service_id": os.getenv("RENDER_SERVICE_ID", "unknown"),
            "file": os.path.basename(__file__),
            "pwd": os.getcwd(),
        }
    ), 200


@app.route("/api/ping", methods=["GET"])
def ping():
    return jsonify({"status": "success", "message": "ping-ok-2025-12-23"}), 200


# -----------------------------------------------------------------------------
# OpenAI client
# -----------------------------------------------------------------------------

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# You can override models in Render env if you want
MODEL_OUTLINE_JSON = os.getenv("OPENAI_MODEL_OUTLINE_JSON", "gpt-4.1-mini")
MODEL_DRAFT = os.getenv("OPENAI_MODEL_DRAFT", "gpt-4.1-mini")
MODEL_LEGACY = os.getenv("OPENAI_MODEL_LEGACY", "gpt-4.1")
MODEL_TRANSCRIBE = os.getenv("OPENAI_MODEL_TRANSCRIBE", "gpt-4o-transcribe")

# Source text limit (chars). You can override via Render env.
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "4000"))

# -----------------------------------------------------------------------------
# Postgres / Neon DB
# -----------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db():
    """
    Open a new Postgres connection.
    Uses dict_row so rows come back as dicts.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in Render -> Environment.")
    # psycopg v3:
    # - row_factory=dict_row makes fetchone()/fetchall() return dict-like rows
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def row_to_dict(row):
    return dict(row) if row else None


def init_db():
    """Create tables if they don't exist."""
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

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

        conn.commit()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# Initialize DB tables at startup
init_db()

# -----------------------------------------------------------------------------
# Health check
# -----------------------------------------------------------------------------

@app.route("/")
def index():
    return "BlueMarble AI Ebook backend is running.", 200


# -----------------------------------------------------------------------------
# Upload / Transcribe (filesystem) - NOTE: Render disk is ephemeral unless you add a disk/S3
# -----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"status": "error", "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"status": "error", "error": "Empty filename"}), 400

    filename = f.filename
    save_path = os.path.join(UPLOAD_DIR, filename)
    f.save(save_path)

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
                model=MODEL_TRANSCRIBE,
                file=audio_file,
            )
        transcript_text = getattr(result, "text", None) or str(result)
        return jsonify({"status": "success", "transcript": transcript_text}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# -----------------------------------------------------------------------------
# Legacy endpoints (kept)
# -----------------------------------------------------------------------------

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
            model=MODEL_LEGACY,
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
            model=MODEL_LEGACY,
            messages=[
                {"role": "system", "content": "You write detailed, well-structured ebook chapters."},
                {"role": "user", "content": prompt},
            ],
        )
        chapter = response.choices[0].message.content
        return jsonify({"status": "success", "chapter": chapter}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# -----------------------------------------------------------------------------
# New project flow: Projects + Text -> Outline JSON -> Chapter Drafts
# -----------------------------------------------------------------------------

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

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO book_projects
                (title, subtitle, target_audience, tone, language,
                 word_count_target, outline_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *;
            """,
            (title, subtitle, target_audience, tone, language, word_count_target, None, created_at, updated_at),
        )
        row = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "project": row_to_dict(row)}), 201
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM book_projects ORDER BY created_at DESC")
        rows = cur.fetchall()
        return jsonify({"status": "success", "projects": [row_to_dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def get_project(project_id):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"status": "error", "error": "Project not found"}), 404

        project = row_to_dict(row)

        cur.execute("SELECT COUNT(*) AS cnt FROM source_documents WHERE project_id = %s", (project_id,))
        project["source_document_count"] = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM chapters WHERE project_id = %s", (project_id,))
        project["chapter_count"] = cur.fetchone()["cnt"]

        return jsonify({"status": "success", "project": project}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/add-text", methods=["POST"])
def add_text_source(project_id):
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text is required"}), 400

    label = (data.get("label") or "").strip() or "Untitled source"
    now = now_iso()

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
        if cur.fetchone() is None:
            return jsonify({"status": "error", "error": "Project not found"}), 404

        cur.execute(
            """
            INSERT INTO source_documents
                (project_id, label, content_text, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *;
            """,
            (project_id, label, text, now, now),
        )
        row = cur.fetchone()
        conn.commit()
        return jsonify({"status": "success", "source_document": row_to_dict(row)}), 201
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/sources", methods=["GET"])
def list_sources(project_id):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
        if cur.fetchone() is None:
            return jsonify({"status": "error", "error": "Project not found"}), 404

        cur.execute(
            """
            SELECT * FROM source_documents
            WHERE project_id = %s
            ORDER BY created_at ASC
            """,
            (project_id,),
        )
        rows = cur.fetchall()
        return jsonify({"status": "success", "source_documents": [row_to_dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/build-outline", methods=["POST"])
def build_outline_for_project(project_id):
    """
    Build a JSON outline using all source documents and write chapters.
    IMPORTANT: This function is atomic: if anything fails, DB changes are rolled back.
    """
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
        project_row = cur.fetchone()
        if project_row is None:
            return jsonify({"status": "error", "error": "Project not found"}), 404

        project = row_to_dict(project_row)

        cur.execute(
            """
            SELECT content_text
            FROM source_documents
            WHERE project_id = %s
            ORDER BY created_at ASC
            """,
            (project_id,),
        )
        source_rows = cur.fetchall()
        if not source_rows:
            return jsonify({"status": "error", "error": "No source documents found for project"}), 400

        full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()
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

        response = client.chat.completions.create(
            model=MODEL_OUTLINE_JSON,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
        )
        outline_json_str = response.choices[0].message.content
        outline_data = json.loads(outline_json_str)

        chapters = outline_data.get("chapters") or []
        if not isinstance(chapters, list) or not chapters:
            return jsonify({"status": "error", "error": "Model did not return a valid 'chapters' list in JSON."}), 500

        now = now_iso()

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
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *;
                """,
                (project_id, order, title, summary, None, now, now),
            )
            saved_chapters.append(row_to_dict(cur.fetchone()))

        cur.execute(
            "UPDATE book_projects SET outline_json = %s, updated_at = %s WHERE id = %s",
            (json.dumps(outline_data), now, project_id),
        )

        conn.commit()

        saved_chapters_sorted = sorted(saved_chapters, key=lambda c: c["chapter_order"])
        return jsonify({"status": "success", "outline": outline_data, "chapters": saved_chapters_sorted}), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "error": f"build-outline failed: {e}"}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/chapters", methods=["GET"])
def list_chapters_for_project(project_id):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT id FROM book_projects WHERE id = %s", (project_id,))
        if cur.fetchone() is None:
            return jsonify({"status": "error", "error": "Project not found"}), 404

        cur.execute(
            """
            SELECT * FROM chapters
            WHERE project_id = %s
            ORDER BY chapter_order ASC
            """,
            (project_id,),
        )
        rows = cur.fetchall()
        return jsonify({"status": "success", "chapters": [row_to_dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/chapters/<int:chapter_id>", methods=["GET"])
def get_project_chapter(project_id, chapter_id):
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT * FROM chapters
            WHERE project_id = %s AND id = %s
            """,
            (project_id, chapter_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"status": "error", "error": "Chapter not found"}), 404

        return jsonify({"status": "success", "chapter": row_to_dict(row)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/chapters/<int:chapter_id>/generate-draft", methods=["POST"])
def generate_chapter_draft(chapter_id):
    """
    Generate a draft for a single chapter.
    If OpenAI fails: returns 500 and does NOT write to DB.
    """
    conn = None
    cur = None
    try:
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
        row = cur.fetchone()
        if row is None:
            return jsonify({"status": "error", "error": "Chapter not found"}), 404

        chapter = row_to_dict(row)

        cur.execute(
            """
            SELECT content_text
            FROM source_documents
            WHERE project_id = %s
            ORDER BY created_at ASC
            """,
            (chapter["project_id"],),
        )
        source_rows = cur.fetchall()

        full_text = "\n\n".join(r["content_text"] for r in source_rows if r.get("content_text"))
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

        resp = client.chat.completions.create(
            model=MODEL_DRAFT,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            temperature=0.7,
        )
        draft_text = resp.choices[0].message.content

        now = now_iso()
        cur.execute(
            """
            UPDATE chapters
            SET draft_text = %s, updated_at = %s
            WHERE id = %s
            """,
            (draft_text, now, chapter_id),
        )
        conn.commit()

        return jsonify({"status": "success", "chapter_id": chapter_id, "updated_at": now}), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(
            {
                "status": "error",
                "error": "Failed to generate chapter draft",
                "details": str(e),
            }
        ), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/api/projects/<int:project_id>/generate-chapters", methods=["POST"])
def generate_chapters_for_project(project_id):
    """
    Generate ONE chapter draft per call – the first chapter that does not yet have draft_text.
    If OpenAI fails: returns 500 and does NOT write to DB.
    """
    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM book_projects WHERE id = %s", (project_id,))
        project_row = cur.fetchone()
        if project_row is None:
            return jsonify({"status": "error", "error": "Project not found"}), 404
        project = row_to_dict(project_row)

        cur.execute(
            """
            SELECT content_text
            FROM source_documents
            WHERE project_id = %s
            ORDER BY created_at ASC
            """,
            (project_id,),
        )
        source_rows = cur.fetchall()
        if not source_rows:
            return jsonify({"status": "error", "error": "No source documents found for project"}), 400

        full_text = "\n\n".join(r["content_text"] for r in source_rows).strip()
        limited_text = full_text[:MAX_SOURCE_CHARS]

        cur.execute(
            "SELECT * FROM chapters WHERE project_id = %s ORDER BY chapter_order ASC",
            (project_id,),
        )
        chapter_rows = cur.fetchall()
        if not chapter_rows:
            return jsonify({"status": "error", "error": "No chapters found for project"}), 400

        chapters = [row_to_dict(r) for r in chapter_rows]
        target_chapter = next((c for c in chapters if not c.get("draft_text")), None)
        if target_chapter is None:
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
            f"Chapter {target_chapter['chapter_order']}: {target_chapter['title']}\n"
            f"Chapter summary: {target_chapter.get('summary') or 'No summary provided.'}\n\n"
            "Source material from the author (notes, transcripts, etc.):\n"
            f"{limited_text}\n\n"
            "Write a complete, well-structured chapter based on the chapter title, "
            "summary, and source material. Make it coherent, readable, and grounded "
            "in the source material where possible.\n"
            "- 800–1,200 words\n"
            "- Use short paragraphs and subheadings\n"
        )

        resp = client.chat.completions.create(
            model=MODEL_DRAFT,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
            temperature=0.7,
        )
        draft_text = resp.choices[0].message.content

        now = now_iso()
        cur.execute(
            """
            UPDATE chapters
            SET draft_text = %s, updated_at = %s
            WHERE id = %s
            """,
            (draft_text, now, target_chapter["id"]),
        )
        conn.commit()

        cur.execute("SELECT * FROM chapters WHERE id = %s", (target_chapter["id"],))
        updated = row_to_dict(cur.fetchone())

        return jsonify({"status": "success", "generated_chapters": [updated]}), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify(
            {
                "status": "error",
                "error": "generate-chapters failed",
                "details": str(e),
            }
        ), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# -----------------------------------------------------------------------------
# Local dev entrypoint (Render uses: gunicorn backend:app)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
