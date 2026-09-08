# compress.pdf

A fast, in-memory PDF compressor tuned for scanned/photographed documents,
deployable on Vercel with no database and no persistent storage.

## How it works

`POST /api/compress` (Python serverless function) receives a PDF as
`multipart/form-data` along with a `level` field (`low` / `medium` / `high`).

- It samples the first few pages to guess whether the PDF is **scanned**
  (little to no extractable text) or **digital** (has a real text layer).
- **Scanned pages** are re-rendered at a level-appropriate DPI and
  re-encoded as JPEG, then rebuilt into a new PDF. This is where the real
  size savings come from for photographed/scanned forms and ID images.
- **Digital pages** keep their text layer intact — only their embedded
  images are downscaled/recompressed, so real text and vector content
  isn't rasterized.
- Metadata is stripped and the PDF is rebuilt with stream compression
  (`garbage=4, deflate=True, clean=True`).
- The compressed PDF is streamed straight back in the HTTP response.
  Nothing is written to disk, and the process holds no state between
  requests.

Every response gets a brand-new, alphabet-only filename (e.g.
`QwertyUiOpAsdf.pdf`) via `Content-Disposition`, even for repeated uploads
of the exact same file.

## Project structure

```
/api
  compress.py       # the serverless compression endpoint
/public
  index.html
  style.css
  app.js
requirements.txt
vercel.json
```

## Deploy to Vercel

1. Push this project to a GitHub repo.
2. Import the repo in Vercel ("Add New Project").
3. Framework preset: **Other**. No build command is needed — Vercel
   detects `requirements.txt` and installs the Python dependencies for
   `api/compress.py` automatically, and serves everything in `/public`
   as static files.
4. Deploy. Your site will be live at `https://<project>.vercel.app`.

No `npm install` or Node build step is required for this project.

## Configuration notes

- **Max upload size**: set in two places — `MAX_UPLOAD_BYTES` in
  `api/compress.py` and `MAX_UPLOAD_BYTES` in `public/app.js` (keep them
  in sync). Default is 20MB.
- **Vercel body size limits**: Vercel serverless functions have their own
  request body size ceiling depending on your plan (typically a few MB on
  Hobby, higher on Pro/Enterprise). If you need larger uploads, check your
  plan's limits in the Vercel dashboard and raise `MAX_UPLOAD_BYTES`
  accordingly — raising the constant alone won't help if the platform
  rejects the request first.
- **Function timeout/memory**: `vercel.json` sets `maxDuration: 60` and
  `memory: 1536` for `api/compress.py`. Multi-page scanned PDFs at higher
  DPI are the most memory-hungry case — raise `memory` if you see
  out-of-memory errors on very large documents (Pro/Enterprise plans allow
  higher values than Hobby).
- **Compression levels** (DPI / JPEG quality / max image dimension) are
  defined in the `LEVELS` dict in `api/compress.py` — tune them there.

## Security & privacy

- Uploaded bytes are validated (`%PDF-` header check, encrypted-PDF
  rejection) before processing.
- Files exist only in memory for the duration of a single request; nothing
  is persisted, logged to disk, or served back from storage.
- Corrupt, encrypted, or non-PDF uploads return a clear JSON error instead
  of a stack trace.
