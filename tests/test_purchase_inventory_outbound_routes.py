import unittest

import app
import procurement_inventory as pi
from tests import test_purchase_inventory_routes as fixtures


class PurchaseOutboundRouteTests(unittest.TestCase):
    tearDown = fixtures.PurchaseInventoryRouteTests.tearDown
    create_order = fixtures.PurchaseInventoryRouteTests.create_order
    login = fixtures.PurchaseInventoryRouteTests.login
    scalar = fixtures.PurchaseInventoryRouteTests.scalar
    page_data = fixtures.PurchaseInventoryRouteTests.page_data
    payload = fixtures.PurchaseInventoryRouteTests.payload

    def setUp(self):
        # Reuse actual receipt route fixtures without inheriting unrelated tests.
        fixtures.PurchaseInventoryRouteTests.fixture_setup(self)
        self.client.post(self.url, json=self.payload(actual="100", qualified="100"))
        self.lot_id = self.scalar("SELECT id FROM purchase_inventory_lots")
        self.lot_no = self.scalar("SELECT lot_no FROM purchase_inventory_lots")
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_outbound_purchase_inventory=1 WHERE username IN ('receiver','blind')")
        with self.client.session_transaction() as session:
            self.csrf = session["inventory_csrf_token"]
        self.list_url = "/admin/shipping/purchase-goods/raw-material"
        self.new_url = self.list_url + "/new"

    def data(self, **changes):
        data = dict(category="raw_material", idempotency_key="out-1", csrf_token=self.csrf,
                    outbound_at="2026-09-12", used_by="王师傅", operator="forged", remark="车间领用",
                    rows=[dict(lot_id=self.lot_id, expected_version=1, quantity="2", remark="机架")])
        data.update(changes)
        return data

    def test_filters_show_only_selectable_category_stock_without_prices(self):
        self.assertEqual(self.client.get(self.new_url).status_code, 200)
        for filters in (dict(supplier_id="1", order_no="PO-RAW", q="AB", location_id=self.location_id), {}):
            page = self.client.get(self.new_url, query_string=filters)
            self.assertIn(self.lot_no, page.text)
            for value in ("实际镀锌板", "供应商甲", "PO-RAW", "原料区", "经办人", 'readonly'):
                self.assertIn(value, page.text)
            for secret in ("unit_price", "amount_minor", "9876.54", "987654", "单价"):
                self.assertNotIn(secret, page.text)
        for filters in (dict(supplier_id=2), dict(q="absent"), dict(order_no="absent"), dict(location_id=999)):
            self.assertNotIn(self.lot_no, self.client.get(self.new_url, query_string=filters).text)
        for category in ("carton", "outsourcing", "other"):
            self.assertNotIn(self.lot_no, self.client.get(self.new_url.replace("raw-material", category)).text)
        self.assertEqual(self.client.get(self.new_url.replace("raw-material", "bad")).status_code, 404)
        with app.get_db() as conn:
            conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=?", (self.location_id,))
        self.assertNotIn(self.lot_no, self.client.get(self.new_url).text)

    def test_post_commits_retry_and_changed_key_conflict(self):
        first = self.client.post(self.new_url, json=self.data())
        self.assertEqual(first.status_code, 201)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 98)
        self.assertEqual(self.scalar("SELECT operator FROM purchase_inventory_outbounds"), "receiver")
        retry = self.client.post(self.new_url, json=self.data())
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json["duplicate"])
        self.assertEqual(retry.json["id"], first.json["id"])
        changed = self.client.post(self.new_url, json=self.data(used_by="李师傅"))
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 1)

    def test_stale_conflict_refreshes_all_price_free_availability(self):
        with app.get_db() as conn:
            pi.adjust_purchase_inventory(conn, self.lot_id, 5, "盘点", "keeper", "2026-09-12", 1, "adjust")
        response = self.client.post(self.new_url, json=self.data())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["candidates"][0]["available_quantity"], 5)
        self.assertEqual(response.json["candidates"][0]["version"], 2)
        self.assertFalse(response.json["needs_confirmation"])
        for secret in ("unit_price", "amount_minor", "987654", "currency"):
            self.assertNotIn(secret, response.text)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 0)

    def test_permissions_csrf_and_category_cannot_be_bypassed(self):
        self.assertEqual(self.client.post(self.new_url, json=self.data(csrf_token="bad")).status_code, 403)
        self.assertEqual(self.client.post(self.new_url, json=self.data(category="carton")).status_code, 409)
        self.assertEqual(self.client.post(self.new_url, json=[]).status_code, 400)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 0)
        first = self.client.post(self.new_url, json=self.data())
        self.assertEqual(first.status_code, 201)
        detail = first.json["redirect_url"]
        self.assertEqual(self.client.post(detail + "/void", json={"csrf_token": "bad"}).status_code, 403)
        self.assertEqual(self.client.get(detail + "/void").status_code, 405)
        self.login("reader")
        for url in (self.list_url, self.new_url, detail, detail + "/export.xlsx", detail + "/export.pdf"):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(self.new_url, json=self.data()).status_code, 302)
        self.assertEqual(self.client.post(detail + "/void", json=self.data()).status_code, 302)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 98)

    def test_outbound_only_navigation_does_not_grant_customer_shipping(self):
        with app.get_db() as conn:
            flags = [r["name"] for r in conn.execute("PRAGMA table_info(users)")
                     if r["name"].startswith("can_") and r["name"] != "can_outbound_purchase_inventory"]
            conn.execute("UPDATE users SET " + ",".join(f"{f}=0" for f in flags) + " WHERE username='blind'")
        self.login("blind")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        dashboard = response.text
        self.assertIn('href="' + self.list_url + '"', dashboard.split("<nav>", 1)[1].split("</nav>", 1)[0])
        self.assertIn('href="' + self.list_url + '"', dashboard.split('<section class="module-grid">', 1)[1])
        page = self.client.get(self.list_url)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('href="/admin/shipped-orders"', page.text)
        self.assertEqual(self.client.get("/admin/shipped-orders").status_code, 302)
        self.assertEqual(self.scalar("SELECT can_manage_shipped + can_view_shipped FROM users WHERE username='blind'"), 0)

    def test_detail_history_exports_use_snapshots_after_master_changes(self):
        first = self.client.post(self.new_url, json=self.data())
        self.assertEqual(first.status_code, 201)
        detail = first.json["redirect_url"]
        with app.get_db() as conn:
            conn.execute("UPDATE suppliers SET name='NEW SUPPLIER' WHERE id=1")
            conn.execute("UPDATE purchase_orders SET order_no='NEW ORDER'")
            conn.execute("UPDATE warehouse_locations SET name='NEW LOCATION'")
            conn.execute("UPDATE purchase_inventory_lots SET item_name='NEW ITEM',supplier_name='NEW LOT SUPPLIER',location_name='NEW LOT LOCATION'")
        page = self.client.get(detail)
        for value in ("实际镀锌板", "供应商甲", "原料区", "PO-RAW", self.lot_no, "王师傅", "receiver", "机架"):
            self.assertIn(value, page.text)
        for value in ("NEW SUPPLIER", "NEW ORDER", "NEW LOCATION", "NEW ITEM", "NEW LOT", "987654", "单价"):
            self.assertNotIn(value, page.text)
        history = self.client.get(self.list_url, query_string={"order_no": "PO-RAW", "supplier_id": 1, "q": "实际镀锌", "location_id": self.location_id})
        self.assertIn(first.json["outbound_no"], history.text)
        self.assertNotIn(first.json["outbound_no"], self.client.get(self.list_url, query_string={"q": "absent"}).text)
        self.assertNotIn(first.json["outbound_no"], self.client.get(self.list_url.replace("raw-material", "carton")).text)
        from openpyxl import load_workbook
        from io import BytesIO
        export = self.client.get(detail + "/export.xlsx")
        self.assertEqual(export.status_code, 200)
        ws = load_workbook(BytesIO(export.data)).active
        values = "".join(" ".join(str(c.value) for row in ws for c in row).split())
        for value in ("实际镀锌板", "供应商甲", "原料区", "PO-RAW", self.lot_no):
            self.assertIn(value, values)
        self.assertNotIn("NEW", values)
        pdf = self.client.get(detail + "/export.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertEqual(self.client.get(detail + "/export.csv").status_code, 404)

    def test_export_responses_never_allow_shared_caching(self):
        posted = self.client.post(self.new_url, json=self.data())
        self.assertEqual(posted.status_code, 201)
        target = posted.json["redirect_url"]
        for suffix in ("xlsx", "pdf", "bad"):
            response = self.client.get(target + "/export." + suffix)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.login("reader")
        self.assertIn("no-store", self.client.get(target + "/export.xlsx").headers.get("Cache-Control", ""))

    def test_void_restores_once_even_when_location_disabled(self):
        first = self.client.post(self.new_url, json=self.data())
        self.assertEqual(first.status_code, 201)
        detail = first.json["redirect_url"]
        with app.get_db() as conn:
            conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=?", (self.location_id,))
        self.assertIn('action="' + detail + '/void"', self.client.get(detail).text)
        for _ in range(2):
            result = self.client.post(detail + "/void", json={"csrf_token": self.csrf})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["status"], "voided")
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions WHERE transaction_type='reversal'"), 1)
        self.assertNotIn('action="' + detail + '/void"', self.client.get(detail).text)
        self.assertEqual(self.client.get("/admin/shipping/purchase-goods/records/99999").status_code, 404)

    def test_post_void_leave_customer_finance_and_product_tables_unchanged(self):
        def snapshot():
            with app.get_db() as conn:
                names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                         if not r[0].startswith(("purchase_inventory_", "sqlite_"))]
                return {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in names}
        before = snapshot()
        first = self.client.post(self.new_url, json=self.data())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.client.post(first.json["redirect_url"] + "/void", json={"csrf_token": self.csrf}).status_code, 200)
        self.assertEqual(snapshot(), before)

    def test_four_categories_post_their_own_lots_and_preserve_dimensions(self):
        lots = {"raw_material": self.lot_id}
        for category in ("carton", "outsourcing", "other"):
            oid, iid = self.create_order(category, order_no="PO-" + category)
            with app.get_db() as conn:
                pi.post_purchase_receipt(conn, dict(category=category,
                    preview_token=pi.receipt_preview_token(pi.load_receipt_preview(conn, oid)),
                    idempotency_key="receipt-" + category, received_at="2026-09-12", rows=[dict(
                        purchase_order_item_id=iid, item_name=category + "实际", drawing_no="D-" + category,
                        material="AB", length=10, width=20, height=30, actual_quantity=3, qualified_quantity=3,
                        location_id=self.location_id, invoice_status="pending")]), "receiver", "2026-09-12T10:00:00")
                lots[category] = conn.execute("SELECT id FROM purchase_inventory_lots WHERE purchase_order_id=?", (oid,)).fetchone()[0]
        for category, lot_id in lots.items():
            target = self.new_url.replace("raw-material", category.replace("_", "-"))
            response = self.client.post(target, json=self.data(category=category, idempotency_key="out-" + category,
                rows=[dict(lot_id=lot_id, expected_version=1, quantity=2, remark="本类领用")]))
            self.assertEqual(response.status_code, 201)
            page = self.client.get(response.json["redirect_url"])
            self.assertEqual(page.status_code, 200)
            if category in {"raw_material", "carton"}:
                self.assertIn("厚度 mm" if category == "raw_material" else "高 mm", page.text)
                self.assertNotIn("高 mm" if category == "raw_material" else "厚度 mm", page.text)

    def test_multi_lot_insufficient_rolls_back_and_html_escapes_business_text(self):
        # The order is fully received, so this fixture needs explicit over-receipt confirmation.
        with app.get_db() as conn:
            pi.post_purchase_receipt(conn, dict(
                preview_token=pi.receipt_preview_token(pi.load_receipt_preview(conn, self.order_id)),
                idempotency_key="receipt-confirmed", received_at="2026-09-12", confirm_over_receipt=True,
                rows=[dict(purchase_order_item_id=self.item_id, item_name="实际板", material="AB", length=10,
                           width=20, thickness=1, actual_quantity=5, qualified_quantity=5,
                           location_id=self.location_id, invoice_status="pending")]), "receiver", "2026-09-12T10:00:00")
            second = conn.execute("SELECT MAX(id) FROM purchase_inventory_lots").fetchone()[0]
        rows = [dict(lot_id=self.lot_id, expected_version=1, quantity=1), dict(lot_id=second, expected_version=1, quantity=6)]
        self.assertEqual(self.client.post(self.new_url, json=self.data(rows=rows)).status_code, 409)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", self.lot_id), 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 0)
        result = self.client.post(self.new_url, json=self.data(used_by='<script>alert(1)</script>', remark='<img src=x onerror=alert(2)>'))
        self.assertEqual(result.status_code, 201)
        page = self.client.get(result.json["redirect_url"]).text
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
