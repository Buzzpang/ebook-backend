# app/services/whisper_service.py

from openai import OpenAI
from app.config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)

def transcribe_audio(file_path):
    with open(file_path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file,
            response_format="text"
        )
    return result
