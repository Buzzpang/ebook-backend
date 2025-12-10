from flask import Blueprint, request, jsonify
from app.services.gpt_service import gpt_service

chapter_bp = Blueprint("chapter_bp", __name__)

@chapter_bp.route("/generate-chapter", methods=["POST"])
def generate_chapter_route():
    data = request.get_json()

    if not data or "title" not in data or "description" not in data:
        return jsonify({
            "error": "Missing fields. Required: 'title' and 'description'."
        }), 400

    title = data["title"]
    description = data["description"]

    prompt = f"""
    Write a complete, well-written chapter for a book.

    CHAPTER TITLE: {title}

    DESCRIPTION / REQUIREMENTS:
    {description}

    The chapter must:
    - Follow the style of a professional business book
    - Use clear subsections and logical flow
    - Be highly readable and actionable
    - Include examples when helpful
    """

    response = gpt_service.chat(
        [
            {"role": "system", "content": "You are a world-class business book ghostwriter."},
            {"role": "user", "content": prompt}
        ]
    )

    return jsonify({
        "chapter": response.get("content"),
        "model_used": response.get("model_used"),
        "usage": response.get("usage")
    })
