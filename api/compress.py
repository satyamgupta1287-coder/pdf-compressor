from http.server import BaseHTTPRequestHandler
from io import BytesIO
from pypdf import PdfReader, PdfWriter
import secrets
import string
import cgi


def random_filename():
    letters = string.ascii_letters
    name = "".join(secrets.choice(letters) for _ in range(12))
    return name + ".pdf"


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            content_type = self.headers.get("Content-Type", "")

            if "multipart/form-data" not in content_type:
                self.send_error(400, "Invalid upload")
                return

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "")
                }
            )

            if "pdf" not in form:
                self.send_error(400, "PDF file is required")
                return

            file_item = form["pdf"]

            if not file_item.file:
                self.send_error(400, "Invalid PDF")
                return

            pdf_data = file_item.file.read()

            if not pdf_data.startswith(b"%PDF"):
                self.send_error(400, "Only PDF files are allowed")
                return

            # Read PDF
            reader = PdfReader(BytesIO(pdf_data))

            # Create optimized/lossless copy
            writer = PdfWriter()

            for page in reader.pages:
                writer.add_page(page)

            # Remove unnecessary metadata
            writer.add_metadata({})

            output = BytesIO()
            writer.write(output)

            compressed_pdf = output.getvalue()

            filename = random_filename()

            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"'
            )
            self.send_header(
                "Content-Length",
                str(len(compressed_pdf))
            )
            self.end_headers()

            self.wfile.write(compressed_pdf)

        except Exception as e:
            self.send_error(500, "Could not process PDF")
