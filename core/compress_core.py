"""
core/compress_core.py

Framework-agnostic PDF compression logic. Both the Vercel serverless
entrypoint (api/compress.py) and the Render/Flask entrypoint (app.py)
import from here, so the actual compression behavior is identical no
matter which platform is serving the request.

compress_pdf(file_bytes, strength) is the single entrypoint other
modules should call.
"""

import io
import random
import string
import fitz  # PyMuPDF
from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB hard cap (tune per host/plan)

# Continuous compression control. strength=0 keeps the most quality/least
# compression; strength=100 pushes for the smallest possible file. Scanned
# pages are fully re-rasterized at the resulting dpi/quality/max_image_dim.
# Digital/text pages only have their embedded images recompressed at them.
STRENGTH_MIN_DPI, STRENGTH_MAX_DPI = 72, 220
STRENGTH_MIN_QUALITY, STRENGTH_MAX_QUALITY = 22, 90
STRENGTH_MIN_DIM, STRENGTH_MAX_DIM = 900, 2400
DEFAULT_STRENGTH = 50

# Legacy named presets, kept for backward compatibility / quick-select
# buttons on the frontend. Each maps to an equivalent strength value.
LEVEL_TO_STRENGTH = {"low": 20, "medium": 50, "high": 80}
DEFAULT_LEVEL = "medium"

# A page is treated as "scanned" (raster-recompress the whole page) when the
# average extractable text per sampled page is below this many characters.
SCANNED_TEXT_THRESHOLD = 30
SAMPLE_PAGES = 3  # fewer pages sampled = faster scanned/digital detection


def settings_from_strength(strength: int):
    """Map a 0-100 strength value to dpi / jpg_quality / max_image_dim."""
    strength = max(0, min(100, strength))
    t = strength / 100.0
    dpi = round(STRENGTH_MAX_DPI - t * (STRENGTH_MAX_DPI - STRENGTH_MIN_DPI))
    quality = round(
        STRENGTH_MAX_QUALITY - t * (STRENGTH_MAX_QUALITY - STRENGTH_MIN_QUALITY)
    )
    max_dim = round(STRENGTH_MAX_DIM - t * (STRENGTH_MAX_DIM - STRENGTH_MIN_DIM))
    return {"dpi": dpi, "jpg_quality": quality, "max_image_dim": max_dim}


def resolve_strength(strength_raw, level_raw):
    """Turn the raw 'strength' and/or legacy 'level' form fields into a
    clamped 0-100 int. Prefers `strength` when both are present."""
    if strength_raw is not None:
        try:
            strength = int(round(float(strength_raw)))
        except (TypeError, ValueError):
            strength = DEFAULT_STRENGTH
    else:
        level = (level_raw or DEFAULT_LEVEL).lower()
        strength = LEVEL_TO_STRENGTH.get(level, DEFAULT_STRENGTH)
    return max(0, min(100, strength))


def random_alpha_filename():
    """Generate a fresh, alphabet-only filename like 'QwertyUiOpAsdf.pdf'."""
    length = random.randint(10, 16)
    name = "".join(random.choices(string.ascii_letters, k=length))
    return f"{name}.pdf"


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
            # optimize=False trades a little size for meaningfully faster
            # encoding — matters most on multi-page scanned documents.
            pil_img.save(out, format="JPEG", quality=quality, optimize=False)
            new_bytes = out.getvalue()

            # Only replace if we actually made it smaller
            if len(new_bytes) < len(img_bytes):
                page.replace_image(xref, stream=new_bytes)
        except Exception:
            # Skip any image we can't safely recompress (masks, CMYK, etc.)
            continue


def rasterize_page(new_doc, page, settings):
    """Scanned page: render the whole page to a JPEG and rebuild it."""
    dpi = settings["dpi"]
    quality = settings["jpg_quality"]

    rect = page.rect
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)

    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    out = io.BytesIO()
    # optimize=False is the single biggest speed lever here — on a
    # multi-page scanned PDF it cuts total processing time by roughly a
    # third, at the cost of a modest size increase (~8-10%).
    pil_img.save(out, format="JPEG", quality=quality, optimize=False)
    jpeg_bytes = out.getvalue()

    new_page = new_doc.new_page(width=rect.width, height=rect.height)
    new_page.insert_image(new_page.rect, stream=jpeg_bytes)


def compress_pdf(file_bytes: bytes, strength: int):
    """Compress a PDF's bytes at the given 0-100 strength. Returns bytes.

    Raises ValueError("ENCRYPTED") for password-protected PDFs,
    ValueError("EMPTY") for zero-page PDFs, or lets fitz's own exceptions
    propagate for corrupt/unreadable files.
    """
    settings = settings_from_strength(strength)

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
            rasterize_page(new_doc, src_doc[i], settings)
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


def percent_saved(original_size: int, compressed_size: int) -> float:
    if compressed_size >= original_size:
        return 0.0
    return round((1 - (compressed_size / original_size)) * 100, 1)
