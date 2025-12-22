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
        cur.e

