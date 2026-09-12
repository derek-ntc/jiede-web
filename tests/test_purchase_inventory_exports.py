"""Inventory PDF uses the same filtered snapshot rows and price boundary as XLSX."""
from io import BytesIO
import re
import shutil
import subprocess
import unittest

import app
import procurement_documents as documents
import procurement_inventory as pi
from tests import test_purchase_inventory_routes as fixtures
from tests.test_purchase_order_exports import pdf_pages, workbook_text
from tests.test_purchase_receipt_exports import sample_receipt, receipt_items


class PurchaseInventoryPdfTests(unittest.TestCase):
    def test_pdf_keeps_dates_and_currency_values_on_one_line(self):
        executable = shutil.which("pdftotext")
        if not executable:
            self.skipTest("PDF text QA requires Poppler pdftotext")
        row = dict(receipt_items()[0], **sample_receipt(), opening_quantity=98, available_quantity=75,
                   unit_price_minor=87654321, amount_minor=6574074075)
        stream = documents.build_purchase_inventory_pdf(dict(category="raw_material"), [row], include_prices=True)
        result = subprocess.run([executable, "-raw", "-", "-"], input=stream.getvalue(), capture_output=True, check=True)
        text = result.stdout.decode()
        for value in ("2026-09-12", "876,543.21", "65,740,740.75"):
            self.assertIn(value, text)

    def test_all_category_pdfs_are_landscape_chinese_and_price_safe(self):
        builder = getattr(documents, "build_purchase_inventory_pdf", None)
        self.assertTrue(callable(builder), "missing purchase inventory PDF builder")
        row = dict(receipt_items()[0], **sample_receipt(), opening_quantity=98, available_quantity=75,
                   unit_price_minor=87654321, amount_minor=6574074075)
        row["remark"] = "=1+2 <b>原样</b>"
        for category in ("raw_material", "carton", "outsourcing", "other"):
            with self.subTest(category=category):
                stream = builder(dict(category=category, order_no="PO-ORIGINAL", invoice_status="pending"), [row], include_prices=False)
                self.assertRegex(stream.getvalue(), rb"/MediaBox\s*\[\s*0\s+0\s+841\.\d+\s+595\.\d+")
                text = "".join("".join(pdf_pages(stream)).split())
                for value in ("采购库存清单", "实际镀锌板001", "PO-ORIGINAL", "LOT-001", "原库位", "待开票", "98", "75", "=1+2<b>原样</b>"):
                    self.assertIn(value, text)
                for secret in ("单价", "金额", "87654321", "876,543.21", "65,740,740.75", "产品交付时必须标识明确"):
                    self.assertNotIn(secret, text)

    def test_long_remark_pdf_preserves_every_fragment_and_repeats_headers(self):
        builder = getattr(documents, "build_purchase_inventory_pdf", None)
        self.assertTrue(callable(builder), "missing purchase inventory PDF builder")
        row = dict(receipt_items()[0], **sample_receipt(), opening_quantity=98, available_quantity=75,
                   unit_price_minor=87654321, amount_minor=6574074075)
        row["remark"] = "库存备注<tag>&核对。" * 180 + "结束标记"
        pages = pdf_pages(builder(dict(category="raw_material"), [row], include_prices=True))
        self.assertGreater(len(pages), 1)
        text = "".join("".join(pages).split())
        # A page break can bisect a remark fragment; remove repeated headers and
        # page numbers before checking the exact uninterrupted saved text.
        header = "库存批次供应商采购订单到货单入库日期实际资料入库数量当前可用数量当前库位开票状态单价当前金额到货行备注"
        content = re.sub(r"第\d+页", "", text).replace(header, "")
        self.assertEqual(content[content.index("库存备注"):], row["remark"])
        self.assertIn("结束标记", text)
        self.assertIn("876,543.21", text)
        for page in pages:
            self.assertIn("当前可用数量", "".join(page.split()))


class PurchaseInventoryPdfRouteTests(unittest.TestCase):
    fixture_setup = fixtures.PurchaseInventoryRouteTests.fixture_setup
    setUp = fixtures.PurchaseInventoryRouteTests.setUp
    tearDown = fixtures.PurchaseInventoryRouteTests.tearDown
    create_order = fixtures.PurchaseInventoryRouteTests.create_order
    login = fixtures.PurchaseInventoryRouteTests.login
    scalar = fixtures.PurchaseInventoryRouteTests.scalar
    page_data = fixtures.PurchaseInventoryRouteTests.page_data
    payload = fixtures.PurchaseInventoryRouteTests.payload

    def test_four_category_routes_filter_like_xlsx_and_keep_snapshot_values(self):
        lots = {"raw_material": self.lot_no}
        for category in ("carton", "outsourcing", "other"):
            oid, iid = self.create_order(category, order_no="PO-" + category)
            with app.get_db() as conn:
                pi.post_purchase_receipt(conn, dict(preview_token=pi.receipt_preview_token(pi.load_receipt_preview(conn, oid)),
                    idempotency_key="pdf-" + category, received_at="2026-09-12", rows=[dict(
                        purchase_order_item_id=iid, item_name="PDF物品-" + category, material="AB", length=10, width=20,
                        height=30, actual_quantity=3, qualified_quantity=3, location_id=self.location_id,
                        invoice_status="pending", remark="原到货备注")]), "receiver", "2026-09-12T10:00:00")
                lots[category] = conn.execute("SELECT lot_no FROM purchase_inventory_lots WHERE purchase_order_id=?", (oid,)).fetchone()[0]
        with app.get_db() as conn:
            conn.execute("UPDATE suppliers SET name='改名供应商' WHERE id=1")
            conn.execute("UPDATE purchase_orders SET order_no='改名订单-'||id")
            conn.execute("UPDATE warehouse_locations SET name='改名库位' WHERE id=?", (self.location_id,))
        self.login("blind")
        for category, lot_no in lots.items():
            root = "/admin/purchase-inventory/" + category.replace("_", "-")
            for filters, present in ((dict(q=lot_no, supplier_id="1", location_id=self.location_id, invoice_status="pending",
                                          received_from="2026-09-12", received_to="2026-09-12"), True),
                                     (dict(q="absent"), False), (dict(invoice_status="invoiced"), False)):
                with self.subTest(category=category, filters=filters):
                    pdf = self.client.get(root + "/export.pdf", query_string=filters)
                    self.assertEqual(pdf.status_code, 200)
                    self.assertEqual(pdf.mimetype, "application/pdf")
                    self.assertEqual(pdf.headers["Cache-Control"], "private, no-store")
                    pdf_text = "".join("".join(pdf_pages(BytesIO(pdf.data))).split())
                    excel = self.client.get(root + "/export.xlsx", query_string=filters)
                    self.assertEqual(excel.status_code, 200)
                    excel_text = "".join(workbook_text(BytesIO(excel.data)).split())
                    for candidate in lots.values():
                        expected = present and candidate == lot_no
                        # q metadata repeats the selected lot, so compare actual item names.
                        name = "实际镀锌板" if candidate == lots["raw_material"] else "PDF物品-" + next(c for c,l in lots.items() if l == candidate)
                        self.assertEqual(name in pdf_text, expected)
                        self.assertEqual(name in excel_text, expected)
                    for secret in ("单价", "金额", "987654", "9876.54", "9,876.54", "改名"):
                        self.assertNotIn(secret, pdf_text)
                    if present:
                        self.assertIn("原料区", pdf_text)
                        self.assertIn("供应商甲", pdf_text)
                        if category != "raw_material":
                            self.assertIn("原到货备注", pdf_text)
            page = self.client.get(root, query_string={"q": lot_no})
            self.assertIn("export.pdf?q=" + lot_no, page.text)
            self.assertIn(f'data-pdf-preview-url="{root}/export.pdf?q={lot_no}"', page.text)
            self.assertIn('data-pdf-preview-name="采购库存清单"', page.text)
            self.assertIn('data-pdf-preview-dialog', page.text)
            self.assertIn('data-pdf-preview-print', page.text)

    def test_pdf_permission_errors_and_zero_stock_filter_are_private(self):
        url = self.inventory_url + "/export.pdf"
        self.login("reader")
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers["Cache-Control"], "private, no-store")
        self.login("receiver")
        for target in (url + "?received_from=invalid", "/admin/purchase-inventory/invalid/export.pdf"):
            response = self.client.get(target)
            self.assertIn(response.status_code, (400, 404))
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with app.get_db() as conn:
            pi.adjust_purchase_inventory(conn, self.lot_id, 0, "盘点", "keeper", "2026-09-12", 1, "zero")
        for include_zero, present in (("0", False), ("1", True)):
            response = self.client.get(url, query_string={"include_zero": include_zero})
            self.assertEqual(response.status_code, 200)
            text = "".join("".join(pdf_pages(BytesIO(response.data))).split())
            self.assertEqual("实际镀锌板" in text, present)


if __name__ == "__main__":
    unittest.main()
