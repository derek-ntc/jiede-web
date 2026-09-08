import tempfile
import unittest
import os
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import app


class PageElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.details = []
        self.current_detail = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "details":
            self.current_detail = {"attrs": attributes, "text": ""}
            self.details.append(self.current_detail)

    def handle_endtag(self, tag):
        if tag == "details":
            self.current_detail = None

    def handle_data(self, data):
        if self.current_detail is not None:
            self.current_detail["text"] += data


class ProductListLayoutTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"
        self.remark = '请按客户图纸加工，检验后独立包装。<script>alert("备注")</script>'
        with app.get_db() as conn:
            self.manual_id = conn.execute(
                """INSERT INTO manuals (
                    drawing_no, product_name, customer, supplier, model, category,
                    version, remark, filename, original_filename, created_at, updated_at
                ) VALUES ('PART-A', '支架', '客户A', '供应商A', '', '', '', ?, '', '', ?, ?)""",
                (self.remark, "2026-09-04T10:00:00", "2026-09-04T10:00:00"),
            ).lastrowid

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_ready
        self.tmpdir.cleanup()

    def test_filter_controls_have_visible_associated_labels_and_clear_navigation(self):
        response = self.client.get("/admin/products")
        self.assertEqual(response.status_code, 200)
        page = PageElements(response.get_data(as_text=True))
        labels = {attrs["for"] for tag, attrs in page.elements if tag == "label" and "for" in attrs}
        for field in ("q", "supplier", "customer"):
            control = next(attrs for tag, attrs in page.elements if tag in ("input", "select") and attrs.get("name") == field)
            self.assertIn(control.get("id"), labels, f"{field} needs a visible associated filter label")
        self.assertTrue(any(tag == "a" and attrs.get("href") == "/admin/products" for tag, attrs in page.elements))

    def test_compact_remark_can_expand_without_losing_or_interpreting_user_text(self):
        page = PageElements(self.client.get("/admin/products").get_data(as_text=True))
        matching = [detail for detail in page.details if self.remark in detail["text"]]
        self.assertEqual(len(matching), 1, "Full remark must be available in a keyboard-accessible disclosure")
        self.assertNotIn("open", matching[0]["attrs"])
        html = self.client.get("/admin/products").get_data(as_text=True)
        self.assertNotIn('<script>alert("备注")</script>', html)

    def test_page_styles_are_loaded_only_for_products_and_sort_links_keep_filters(self):
        response = self.client.get("/admin/products", query_string={"q": "PART", "customer": "客户A", "supplier": "供应商A"})
        page = PageElements(response.get_data(as_text=True))
        styles = [attrs["href"] for tag, attrs in page.elements if tag == "link" and attrs.get("rel") == "stylesheet"]
        product_styles = [url for url in styles if urlsplit(url).path == "/static/product-list.css"]
        self.assertEqual(len(product_styles), 1)
        with self.client.get(product_styles[0]) as stylesheet:
            self.assertEqual(stylesheet.status_code, 200)
        other_page = self.client.get("/admin/customers").get_data(as_text=True)
        self.assertNotIn("/static/product-list.css", other_page)
        sort_links = [attrs["href"] for tag, attrs in page.elements if tag == "a" and "sort-link" in attrs.get("class", "").split()]
        self.assertTrue(sort_links)
        for href in sort_links:
            query = parse_qs(urlsplit(href).query)
            self.assertEqual(query["q"], ["PART"])
            self.assertEqual(query["customer"], ["客户A"])
            self.assertEqual(query["supplier"], ["供应商A"])

    def test_single_assembly_can_expand_to_show_the_full_drawing_and_quantity(self):
        drawing = 'ASSEMBLY-LONG-DRAWING-1234567890'
        with app.get_db() as conn:
            conn.execute(
                """INSERT INTO product_assembly_components
                   (manual_id, assembly_drawing_no, quantity_per_set, sort_order, created_at, updated_at)
                   VALUES (?, ?, 12, 0, '2026-09-04', '2026-09-04')""",
                (self.manual_id, drawing),
            )
        page = PageElements(self.client.get('/admin/products').get_data(as_text=True))
        matching = [detail for detail in page.details if drawing + ' × 12' in detail['text']]
        self.assertEqual(len(matching), 1)
        self.assertNotIn('open', matching[0]['attrs'])

    def test_product_stylesheet_update_changes_the_browser_cache_version(self):
        root = Path(self.tmpdir.name) / 'assets'
        (root / 'static').mkdir(parents=True)
        for name in ('style.css', 'editor.js', 'product-list.css'):
            path = root / 'static' / name
            path.write_text('/* fixture */')
            os.utime(path, (100, 100))
        with patch.object(app, 'BASE_DIR', root), app.app.test_request_context('/admin/products'):
            before = app.upload_limits()['static_version']
            os.utime(root / 'static/product-list.css', (200, 200))
            after = app.upload_limits()['static_version']
        self.assertEqual(before, 100)
        self.assertEqual(after, 200)


if __name__ == "__main__":
    unittest.main()
