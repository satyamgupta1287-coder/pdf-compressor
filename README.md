# compress.pdf

A fast, in-memory PDF compressor tuned for scanned/photographed documents,
with no database and no persistent storage. Deployable on **either**
Vercel (serverless) **or** Render (always-on) from the same codebase.

## How it works

`POST /api/compress` receives a PDF as `multipart/form-data` along with a
`strength` field (0-100, or the legacy `level` field: `low`/`medium`/`high`).

- It samples the first few pages to guess whether the PDF is **scanned**
  (little to no extractable text) or **digital** (has a real text layer).
- **Scanned pages** are re-rendered at a strength-appropriate DPI and
  re-encoded as JPEG, then rebuilt into a new PDF. This is where the real
  size savings come from for photographed/scanned forms and ID images.
- **Digital pages** keep their text layer intact — only their embedded
  images are downscaled/recompressed, so real text and vector content
  isn't rasterized.
- Metadata is stripped and the PDF is rebuilt with stream compression.
- The compressed PDF is streamed straight back in the HTTP response.
  Nothing is written to disk, and the process holds no state between
  requests.

Every response gets a brand-new, alphabet-only filename (e.g.
`QwertyUiOpAsdf.pdf`), and the frontend also generates a fresh one on
every download click — even repeat downloads of the same compressed file.

## Why two deployment targets

**Vercel** (serverless) spins up a fresh function container on infrequent
traffic — importing PyMuPDF/Pillow and booting the runtime adds latency
on top of actual compression time (a "cold start"). This is the most
likely cause of the slowness you were seeing.

**Render** (or any always-on host) keeps one process warm: PyMuPDF/Pillow
are imported once when the worker boots, and every request after that
only pays for the actual compression — no repeated import/boot overhead.
This will feel noticeably faster and more consistent, **provided you're
on an always-on plan** — Render's free tier still spins a sleeping
service back up after ~15 minutes of inactivity, which reintroduces a
cold start on the next request. A paid "Starter" instance stays warm.

The compression code itself (`core/compress_core.py`) is identical either
way — this isn't a quality tradeoff, purely a hosting one.

## Project structure

```
/api
  compress.py        # Vercel serverless entrypoint (thin HTTP wrapper)
/core
  compress_core.py    # shared compression logic — used by both platforms
/public
  index.html
  style.css
  app.js
app.py                # Render/Flask entrypoint (always-on)
pyproject.toml         # Python deps + entrypoint for Vercel's uv-based builder
vercel.json
requirements.txt       # Python deps for Render's pip-based builder
render.yaml            # Render Blueprint (one-click deploy config)
Procfile               # fallback start command for manual Render setup
```

## Deploy to Render (recommended if speed matters most)

1. Push this project to a GitHub repo.
2. In Render: **New → Blueprint**, point it at the repo. Render reads
   `render.yaml` and provisions the web service automatically
   (`pip install -r requirements.txt`, then
   `gunicorn app:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`).
   No manual configuration needed.
3. **Pick an always-on plan** (e.g. Starter) if you want zero cold starts.
   The free plan works but sleeps after ~15 minutes idle.
4. Deploy. Your site is live at `https://<service>.onrender.com` — the
   frontend and `/api/compress` are both served from this one Flask app.

If you'd rather set it up manually instead of via `render.yaml`: create a
Python web service, build command `pip install -r requirements.txt`,
start command `gunicorn app:app --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
(same as the `Procfile`).

## Deploy to Vercel

1. Push this project to a GitHub repo.
2. Import the repo in Vercel ("Add New Project").
3. Framework preset: **Other**. No build command is needed — Vercel's
   Python builder reads `pyproject.toml`, installs `PyMuPDF`/`Pillow` with
   `uv`, and wires up `api/compress.py` using the
   `[tool.vercel] entrypoint = "api.compress:handler"` declaration.
   Everything in `/public` is served as static files automatically.
4. Deploy. Your site will be live at `https://<project>.vercel.app`.

No `npm install` or Node build step is required for either platform.

## Configuration notes

- **Max upload size**: `MAX_UPLOAD_BYTES` in `core/compress_core.py` is
  the single source of truth now (both `api/compress.py` and `app.py`
  import it) — also keep `MAX_UPLOAD_BYTES` in `public/app.js` in sync.
  Default is 20MB.
- **Vercel body size limits**: Vercel serverless functions have their own
  request body size ceiling depending on your plan. If you need larger
  uploads, check your plan's limits in the Vercel dashboard first —
  raising the constant alone won't help if the platform rejects the
  request before your code runs. Render has no equivalent low ceiling.
- **Function timeout/memory (Vercel)**: `vercel.json` sets
  `maxDuration: 60` and `memory: 3008` for `api/compress.py` — Vercel
  allocates CPU proportionally to memory, so this is also a speed lever,
  not just a memory one. Lower it if your plan doesn't support 3008MB or
  if cost is a concern.
- **Worker count/timeout (Render)**: tune `--workers` / `--threads` /
  `--timeout` in `render.yaml` / `Procfile` based on your instance's CPU
  and expected concurrent uploads.
- **Compression strength curve** (DPI / JPEG quality / max image
  dimension per 0-100 strength value) is defined in
  `core/compress_core.py` — tune it there once, and both platforms pick
  up the change.

## Security & privacy

- Uploaded bytes are validated (`%PDF-` header check, encrypted-PDF
  rejection) before processing.
- Files exist only in memory for the duration of a single request; nothing
  is persisted, logged to disk, or served back from storage.
- Corrupt, encrypted, or non-PDF uploads return a clear JSON error instead
  of a stack trace.
