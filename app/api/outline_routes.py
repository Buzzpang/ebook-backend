from flask import Blueprint, request, jsonify
from app.services.gpt_service import gpt_service

outline_bp = Blueprint("outline_bp", __name__)

@outline_bp.route("/generate-outline", methods=["POST"])
def generate_outline_route():
    data = request.get_json()

    if not data or "text" not in data:
        return jsonify({"error": "Missing 'text' field"}), 400

    prompt = f"""
    Create a well-structured outline for a book or document based on the following content:

    CONTENT:
    {data['text']}

    The outline must include:
    - Major sections
    - Chapters under each section
    - Brief one-line descriptions
    - A logical flow
    """

    response = gpt_service.chat(
        [
            {"role": "system", "content": "You are an expert book planner and editor."},
            {"role": "user", "content": prompt}
        ]
    )

    return jsonify({
        "outline": response.get("content"),
        "model_used": response.get("model_used"),
        "usage": response.get("usage")
    })
