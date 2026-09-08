"""
/api/compress.py

Vercel Python Serverless Function.
Accepts a multipart/form-data POST with fields:
  - file: the PDF to compress
  - level: "low" | "medium" | "high"  (default "medium")

Returns the compressed PDF as a raw binary response with:
  - Content-Type: application/pdf
  - Content-Disposition: attachment; filename="<RandomAlphaOnly>.pdf"
  - X-Original-Size, X-Compressed-Size, X-Percent-Saved, X-Filename headers

No file is ever written to disk. Everything happens in memory and the
process exits when the response is sent, so nothing is retained.
"""

import io
import json
import random
import re
import string
import fitz  # PyMuPDF
from PIL import Image
from http.server import BaseHTTPRequestHandler

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB hard cap (tune for your Vercel plan)

# Per-level tuning. Scanned pages are fully re-rasterized at these settings.
# Digital/text pages only have their embedded images recompressed.
LEVELS = {
    "low": {"dpi": 200, "jpg_quality": 85, "max_image_dim": 2200},
    "medium": {"dpi": 150, "jpg_quality": 65, "max_image_dim": 1600},
    "high": {"dpi": 100, "jpg_quality": 45, "max_image_dim": 1200},
}
DEFAULT_LEVEL = "medium"

# A page is treated as "scanned" (raster-recompress the whole page) when the
# average extractable text per sampled page is below this many characters.
SCANNED_TEXT_THRESHOLD = 30
SAMPLE_PAGES = 5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def random_alpha_filename():
    """Generate a fresh, alphabet-only filename like 'QwertyUiOpAsdf.pdf'."""
    length = random.randint(10, 16)
    name = "".join(random.choices(string.ascii_letters, k=length))
    return f"{name}.pdf"


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
        # Strip leading CRLF left over from the boundary split
        if part.startswith(b"\r\n"):
            part = part[2:]
        # Strip trailing CRLF before the next boundary
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


def recompress_page_images(doc, page, settings):
    """Digital/text page: recompress embedded raster images in place."""
    quality = settings["jpg_quality"]
    max_dim = settings["max_image_dim"]

    for img in page.get_images(full=True):
        xref = img[0]
        try:
            base = doc.extract_image(xref)
            img_bytes = base["image"]
            pil_img = Image.open(io.BytesIO(img_bytes))

            if pil_img.mode in ("RGBA", "P", "LA"):
                pil_img = pil_img.convert("RGB")
            elif pil_img.mode not in ("RGB", "L"):
                pil_img = pil_img.convert("RGB")

            w, h = pil_img.size
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                pil_img = pil_img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS,
                )

            out = io.BytesIO()
            pil_img.save(out, format="JPEG", quality=quality, optimize=True)
            new_bytes = out.getvalue()

            # Only replace if we actually made it smaller
            if len(new_bytes) < len(img_bytes):
                page.replace_image(xref, stream=new_bytes)
        except Exception:
            # Skip any image we can't safely recompress (masks, CMYK, etc.)
            continue


def rasterize_page(src_doc, new_doc, page, settings):
    """Scanned page: render the whole page to a JPEG and rebuild it."""
    dpi = settings["dpi"]
    quality = settings["jpg_quality"]

    rect = page.rect
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)

    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    out = io.BytesIO()
    pil_img.save(out, format="JPEG", quality=quality, optimize=True)
    jpeg_bytes = out.getvalue()

    new_page = new_doc.new_page(width=rect.width, height=rect.height)
    new_page.insert_image(new_page.rect, stream=jpeg_bytes)


def compress_pdf(file_bytes: bytes, level: str):
    settings = LEVELS.get(level, LEVELS[DEFAULT_LEVEL])

    src_doc = fitz.open(stream=file_bytes, filetype="pdf")

    if src_doc.needs_pass:
        src_doc.close()
        raise ValueError("ENCRYPTED")

    page_count = src_doc.page_count
    if page_count == 0:
        src_doc.close()
        raise ValueError("EMPTY")

    sample_n = min(SAMPLE_PAGES, page_count)
    sampled_text_len = sum(
        len(src_doc[i].get_text("text").strip()) for i in range(sample_n)
    )
    avg_text = sampled_text_len / sample_n
    scanned_mode = avg_text < SCANNED_TEXT_THRESHOLD

    if scanned_mode:
        new_doc = fitz.open()
        for i in range(page_count):
            rasterize_page(src_doc, new_doc, src_doc[i], settings)
        new_doc.set_metadata({})
        out = io.BytesIO()
        new_doc.save(out, garbage=4, deflate=True, clean=True)
        new_doc.close()
    else:
        for i in range(page_count):
            recompress_page_images(src_doc, src_doc[i], settings)
        src_doc.set_metadata({})
        out = io.BytesIO()
        src_doc.save(out, garbage=4, deflate=True, clean=True)

    src_doc.close()
    return out.getvalue()


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

        level = fields.get("level", DEFAULT_LEVEL).lower()
        if level not in LEVELS:
            level = DEFAULT_LEVEL

        try:
            compressed_bytes = compress_pdf(file_bytes, level)
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

        # Guard against the rare case where compression didn't help
        # (e.g. an already tiny or heavily optimized file).
        if compressed_size >= original_size:
            percent_saved = 0
        else:
            percent_saved = round((1 - (compressed_size / original_size)) * 100, 1)

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
        self.send_header("X-Percent-Saved", str(percent_saved))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(compressed_bytes)

    def do_GET(self):
        self._send_json_error(405, "Use POST with a multipart/form-data PDF upload.")
