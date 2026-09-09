"""
/api/compress.py

Vercel Python Serverless Function. Thin HTTP wrapper — all actual
compression logic lives in core/compress_core.py, shared with the
Render/Flask entrypoint (app.py) so behavior is identical either way.

Accepts a multipart/form-data POST with fields:
  - file: the PDF to compress
  - strength: "0"-"100" (0 = best quality/least compression,
              100 = smallest file/most compression). Default 50.
  - level: "low" | "medium" | "high" — legacy alternative to `strength`,
           used only if `strength` isn't sent. Default "medium".

Returns the compressed PDF as a raw binary response with:
  - Content-Type: application/pdf
  - Content-Disposition: attachment; filename="<RandomAlphaOnly>.pdf"
  - X-Original-Size, X-Compressed-Size, X-Percent-Saved, X-Filename headers

No file is ever written to disk. Everything happens in memory and the
process exits when the response is sent, so nothing is retained.

NOTE ON SPEED: Vercel Python functions cold-start on infrequent traffic —
importing PyMuPDF/Pillow fresh and booting the container adds latency on
top of the actual compression time. If consistent low latency matters
more than serverless convenience, deploy app.py to Render (or any
always-on host) instead — see README.md.
"""

import json
import re
import sys
import os
from http.server import BaseHTTPRequestHandler

# Make the project root importable so `core.compress_core` resolves
# regardless of how Vercel's Python runtime sets up sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.compress_core import (  # noqa: E402
    MAX_UPLOAD_BYTES,
    compress_pdf,
    percent_saved,
    random_alpha_filename,
    resolve_strength,
)

# ---------------------------------------------------------------------------
# Multipart parsing (Vercel's raw Python runtime has no request-parsing
# helpers built in, unlike Flask/FastAPI, so this is done by hand)
# ---------------------------------------------------------------------------


def parse_multipart(body: bytes, boundary: bytes):
    """Minimal multipart/form-data parser. Returns (fields, files) dicts.

    files[name] = {"filename": str, "content": bytes}
    fields[name] = str
    """
    fields = {}
    files = {}
    delimiter = b"--" + boundary
    raw_parts = body.split(delimiter)
    for raw in raw_parts:
        part = raw
        if part in (b"", b"--", b"--\r\n") or part.startswith(b"--\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        header_blob, content = part.split(b"\r\n\r\n", 1)
        headers_text = header_blob.decode("utf-8", errors="ignore")

        name_match = re.search(r'name="([^"]*)"', headers_text)
        if not name_match:
            continue
        field_name = name_match.group(1)

        filename_match = re.search(r'filename="([^"]*)"', headers_text)
        if filename_match:
            files[field_name] = {
                "filename": filename_match.group(1),
                "content": content,
            }
        else:
            fields[field_name] = content.decode("utf-8", errors="ignore")
    return fields, files


# ---------------------------------------------------------------------------
# HTTP handler (Vercel Python runtime convention)
# ---------------------------------------------------------------------------


class handler(BaseHTTPRequestHandler):
    def _send_json_error(self, status, message):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send_json_error(400, "Missing or invalid Content-Length.")
            return

        if content_length <= 0:
            self._send_json_error(400, "Empty request body.")
            return

        if content_length > MAX_UPLOAD_BYTES:
            self._send_json_error(
                413,
                f"File too large. Max upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
            )
            return

        content_type = self.headers.get("Content-Type", "")
        boundary_match = re.search(r"boundary=(.+)", content_type)
        if "multipart/form-data" not in content_type or not boundary_match:
            self._send_json_error(400, "Expected multipart/form-data upload.")
            return

        boundary = boundary_match.group(1).strip().strip('"').encode("utf-8")
        body = self.rfile.read(content_length)

        fields, files = parse_multipart(body, boundary)

        if "file" not in files:
            self._send_json_error(400, "No file field found in upload.")
            return

        file_bytes = files["file"]["content"]
        original_size = len(file_bytes)

        if original_size == 0:
            self._send_json_error(400, "Uploaded file is empty.")
            return

        if not file_bytes.lstrip()[:5].startswith(b"%PDF-"):
            self._send_json_error(400, "Uploaded file is not a valid PDF.")
            return

        strength = resolve_strength(fields.get("strength"), fields.get("level"))

        try:
            compressed_bytes = compress_pdf(file_bytes, strength)
        except ValueError as e:
            if str(e) == "ENCRYPTED":
                self._send_json_error(
                    400, "This PDF is password-protected and cannot be compressed."
                )
            elif str(e) == "EMPTY":
                self._send_json_error(400, "This PDF has no pages.")
            else:
                self._send_json_error(400, "Could not process this PDF.")
            return
        except Exception:
            self._send_json_error(
                422, "This PDF looks corrupt or is in an unsupported format."
            )
            return

        compressed_size = len(compressed_bytes)
        saved_pct = percent_saved(original_size, compressed_size)
        download_name = random_alpha_filename()

        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{download_name}"'
        )
        self.send_header("Content-Length", str(compressed_size))
        self.send_header("X-Filename", download_name)
        self.send_header("X-Original-Size", str(original_size))
        self.send_header("X-Compressed-Size", str(compressed_size))
        self.send_header("X-Percent-Saved", str(saved_pct))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(compressed_bytes)

    def do_GET(self):
        self._send_json_error(405, "Use POST with a multipart/form-data PDF upload.")
