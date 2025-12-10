import os
from flask import Blueprint, request, jsonify, send_file
from datetime import datetime
from docx import Document
from reportlab.pdfgen import canvas

export_bp = Blueprint("export_bp", __name__)

# ---------------------------
# Permanent storage directory
# ---------------------------
EXPORT_DIR = "storage/ebooks"
os.makedirs(EXPORT_DIR, exist_ok=True)


# ---------------------------
# Helper: Save DOCX file
# ---------------------------
def save_docx(text, path):
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(path)


# ---------------------------
# Helper: Save PDF file
# ---------------------------
def save_pdf(text, path):
    c = canvas.Canvas(path)
    y = 800
    for line in text.split("\n"):
        c.drawString(40, y, line)
        y -= 18
        if y < 40:
            c.showPage()
            y = 800
    c.save()


# ---------------------------
# MAIN EXPORT ROUTE
# ---------------------------
@export_bp.route("/export-ebook", methods=["POST"])
def export_ebook():
    data = request.get_json()
    title = data.get("title", "Untitled Ebook")
    content = data.get("content", "")

    # Safe filename
    safe_title = title.replace(" ", "_").replace("/", "_")

    # Generate output file paths
    timestamp = str(int(datetime.now().timestamp()))
    docx_path = os.path.join(EXPORT_DIR, f"{safe_title}_{timestamp}.docx")
    pdf_path = os.path.join(EXPORT_DIR, f"{safe_title}_{timestamp}.pdf")

    # Save documents
    save_docx(content, docx_path)
    save_pdf(content, pdf_path)

    return jsonify({
        "status": "success",
        "message": "Ebook export completed.",
        "title": title,
        "chapter_count": len(content.split("\n\n")),
        "files": {
            "docx": f"/api/export-ebook/download/docx?path={docx_path}",
            "pdf": f"/api/export-ebook/download/pdf?path={pdf_path}",
        }
    })


# ---------------------------
# DOWNLOAD ROUTE
# ---------------------------
@export_bp.route("/export-ebook/download/<filetype>", methods=["GET"])
def download_file(filetype):
    path = request.args.get("path")

    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404

    if filetype == "pdf":
        mimetype = "application/pdf"
    elif filetype == "docx":
        mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        mimetype = "application/octet-stream"

    return send_file(path, as_attachment=True, mimetype=mimetype)
