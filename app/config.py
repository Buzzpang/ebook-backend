import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    # OpenAI
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

    # DigitalOcean Spaces (S3-compatible)
    DO_SPACES_KEY = os.environ.get("DO_SPACES_KEY")
    DO_SPACES_SECRET = os.environ.get("DO_SPACES_SECRET")
    DO_SPACES_REGION = os.environ.get("DO_SPACES_REGION")
    DO_SPACES_BUCKET = os.environ.get("DO_SPACES_BUCKET")

    # Redis / Celery
    CELERY_BROKER_URL = os.environ.get(
        "CELERY_BROKER_URL",
        "redis://localhost:6379/0"
    )
    CELERY_RESULT_BACKEND = os.environ.get(
        "CELERY_RESULT_BACKEND",
        "redis://localhost:6379/0"
    )

    # File storage path (local)
    LOCAL_STORAGE = os.environ.get(
        "LOCAL_STORAGE",
        "./storage"
    )

    # Max file uploads (MB)
    MAX_CONTENT_LENGTH = 1024 * 1024 * 100  # 100 MB
