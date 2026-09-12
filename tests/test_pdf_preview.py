import tempfile
import unittest
from pathlib import Path

import app


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class PdfPreviewTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.original_manuals_dir = app.MANUALS_DIR
        self.original_drawings_dir = app.PRODUCTION_DRAWINGS_DIR
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        app.DB_PATH = root / "manuals.db"
        app.MANUALS_DIR = root / "manuals"
        app.PRODUCTION_DRAWINGS_DIR = root / "production-drawings"
        app.DATABASE_READY = False
        app.init_db()

        self.product_pdf = "product-preview.pdf"
        self.product_dwg = "product-drawing.dwg"
        self.followup_pdf = "followup-preview.pdf"
        self.followup_dwg = "followup-drawing.dwg"
        app.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
        app.PRODUCTION_DRAWINGS_DIR.mkdir(parents=True, exist_ok=True)
        (app.MANUALS_DIR / self.product_pdf).write_bytes(PDF_BYTES)
        (app.MANUALS_DIR / self.product_dwg).write_bytes(b"DWG")
        (app.PRODUCTION_DRAWINGS_DIR / self.followup_pdf).write_bytes(PDF_BYTES)
        (app.PRODUCTION_DRAWINGS_DIR / self.followup_dwg).write_bytes(b"DWG")

        now = "2026-07-21T09:00:00"
        with app.get_db() as conn:
            self.manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    product_name, customer, model, category, version, remark,
                    filename, original_filename, created_at, updated_at, drawing_no
                )
                VALUES ('PDF测试产品', '', '', '', '', '', ?, '产品图纸.pdf', ?, ?, 'PDF-001')
                """,
                (self.product_pdf, now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO manual_files (
                    manual_id, filename, original_filename, file_type, created_at
                )
                VALUES (?, ?, '产品图纸.pdf', 'pdf', ?)
                """,
                (self.manual_id, self.product_pdf, now),
            )
            conn.execute(
                """
                INSERT INTO manual_files (
                    manual_id, filename, original_filename, file_type, created_at
                )
                VALUES (?, ?, '产品图纸.dwg', 'file', ?)
                """,
                (self.manual_id, self.product_dwg, now),
            )
            followup_id = conn.execute(
                """
                INSERT INTO production_followups (
                    batch_no, ordered_at, drawing_no, product_name,
                    created_by, created_at, updated_at
                )
                VALUES ('PF-20260721-001', '2026-07-21', 'PDF-001', 'PDF测试产品', 'admin', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.followup_pdf_id = conn.execute(
                """
                INSERT INTO production_followup_files (
                    followup_id, filename, original_filename, file_type,
                    content_type, file_size, created_at
                )
                VALUES (?, ?, '生产图纸.pdf', 'file', 'application/pdf', ?, ?)
                """,
                (followup_id, self.followup_pdf, len(PDF_BYTES), now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO production_followup_files (
                    followup_id, filename, original_filename, file_type,
                    content_type, file_size, created_at
                )
                VALUES (?, ?, '生产图纸.dwg', 'file', 'application/acad', 3, ?)
                """,
                (followup_id, self.followup_dwg, now),
            )

        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.MANUALS_DIR = self.original_manuals_dir
        app.PRODUCTION_DRAWINGS_DIR = self.original_drawings_dir
        self.tmpdir.cleanup()

    def test_pdf_routes_return_inline_pdf_content(self):
        product_response = self.client.get(f"/manuals/{self.product_pdf}")
        followup_response = self.client.get(
            f"/admin/production-followups/files/{self.followup_pdf_id}"
        )

        self.assertEqual(product_response.mimetype, "application/pdf")
        self.assertIn("inline", product_response.headers.get("Content-Disposition", ""))
        self.assertEqual(followup_response.mimetype, "application/pdf")
        self.assertIn("inline", followup_response.headers.get("Content-Disposition", ""))

    def test_pdf_pages_expose_shared_preview_contract_without_download(self):
        pages = [
            self.client.get("/admin/production-followups?view=legacy").get_data(as_text=True),
            self.client.get(f"/manual/{self.manual_id}/technical").get_data(as_text=True),
            self.client.get(f"/manual/{self.manual_id}/preview").get_data(as_text=True),
        ]

        for html in pages:
            self.assertIn("data-pdf-preview-url=", html)
            self.assertIn("data-pdf-preview-name=", html)
            self.assertIn("data-pdf-preview-dialog", html)
            self.assertIn("data-pdf-preview-print", html)
            self.assertNotIn("data-pdf-preview-download", html)

        production_html, detail_html, preview_html = pages
        self.assertNotIn(
            f'data-pdf-preview-url="/admin/production-followups/files/{self.followup_pdf_id + 1}"',
            production_html,
        )
        for html in (detail_html, preview_html):
            self.assertIn(f'href="/manuals/{self.product_dwg}" target="_blank"', html)

    def test_pdf_preview_script_uses_local_worker_zoom_limits_and_print(self):
        source = (Path(app.app.root_path) / "static" / "pdf-preview.js").read_text()

        self.assertIn("pdf.worker.mjs", source)
        self.assertIn("data-pdf-preview-url", source)
        self.assertIn("MIN_ZOOM = 0.5", source)
        self.assertIn("MAX_ZOOM = 3", source)
        self.assertIn("ZOOM_STEP = 0.25", source)
        self.assertIn("window.print()", source)
        self.assertNotIn("contentWindow.print()", source)


if __name__ == "__main__":
    unittest.main()
