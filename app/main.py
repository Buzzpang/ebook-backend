from flask import Flask, jsonify
from flask_cors import CORS
from app.config import Config

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Enable CORS for local development
    CORS(app)

    # --- Health check route ---
    @app.route("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "message": "BlueMarble AI Backend Running"
        })

    # --- Register API blueprints ---
    from app.api.upload_routes import upload_bp
    from app.api.outline_routes import outline_bp
    from app.api.chapter_routes import chapter_bp
    from app.api.export_routes import export_bp
    from app.routes.transcribe_routes import transcribe_bp


    app.register_blueprint(upload_bp, url_prefix="/api")
    app.register_blueprint(outline_bp, url_prefix="/api")
    app.register_blueprint(chapter_bp, url_prefix="/api")
    app.register_blueprint(export_bp, url_prefix="/api")
    app.register_blueprint(transcribe_bp, url_prefix="/api")


    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
