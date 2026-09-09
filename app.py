"""
app.py

Flask entrypoint for Render (or any always-on host: Railway, Fly.io, a
plain VPS with gunicorn, etc.). Uses the same core/compress_core.py logic
as the Vercel function in api/compress.py, so compression behavior is
identical — the only difference is *how* the process is hosted.

Why this is faster in practice than the Vercel serverless version:
PyMuPDF and Pillow are imported once when the worker boots, not on every
request. On an always-on plan there's no cold start at all after the
first boot — every request just pays the actual compression time.
(A free/sleep-tier Render instance will still cold-start after ~15
minutes of inactivity — see README.md.)

Local dev:
    pip install -r requirements.txt
    python app.py            # http://localhost:8000

Production (what Render runs):
    gunicorn app:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
"""

import os
from flask import Flask, request, Response, jsonify, send_from_directory

from core.compress_core import (
    MAX_UPLOAD_BYTES,
    compress_pdf,
    percent_saved,
    random_alpha_filename,
    resolve_strength,
)

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

app = Flask(__name__, static_folder=PUBLIC_DIR, static_url_path="")
# Reject oversized uploads before Flask even finishes reading the body.
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/api/compress", methods=["POST"])
def api_compress():
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "No file field found in upload."}), 400

    file_bytes = file.read()
    original_size = len(file_bytes)

    if original_size == 0:
        return jsonify({"error": "Uploaded file is empty."}), 400

    if not file_bytes.lstrip()[:5].startswith(b"%PDF-"):
        return jsonify({"error": "Uploaded file is not a valid PDF."}), 400

    strength = resolve_strength(
        request.form.get("strength"), request.form.get("level")
    )

    try:
        compressed_bytes = compress_pdf(file_bytes, strength)
    except ValueError as e:
        if str(e) == "ENCRYPTED":
            return (
                jsonify(
                    {"error": "This PDF is password-protected and cannot be compressed."}
                ),
                400,
            )
        if str(e) == "EMPTY":
            return jsonify({"error": "This PDF has no pages."}), 400
        return jsonify({"error": "Could not process this PDF."}), 400
    except Exception:
        return (
            jsonify({"error": "This PDF looks corrupt or is in an unsupported format."}),
            422,
        )

    compressed_size = len(compressed_bytes)
    saved_pct = percent_saved(original_size, compressed_size)
    download_name = random_alpha_filename()

    response = Response(compressed_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    response.headers["X-Filename"] = download_name
    response.headers["X-Original-Size"] = str(original_size)
    response.headers["X-Compressed-Size"] = str(compressed_size)
    response.headers["X-Percent-Saved"] = str(saved_pct)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(413)
def too_large(_e):
    return (
        jsonify(
            {
                "error": f"File too large. Max upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."
            }
        ),
        413,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
