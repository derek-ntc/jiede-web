import json
import re
import tempfile
import unittest
from pathlib import Path

import app
import procurement as p


NOW = "2026-09-12T10:00:00"


class PurchaseReceiptRouteTests(unittest.TestCase):
    def setUp(self):
        self.original = app.DB_PATH, app.DATABASE_READY, app.app.config["TESTING"], app.app.secret_key
        self.tmp = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmp.name) / "test.db"
        app.DATABASE_READY = False
        app.app.config.update(TESTING=True, SECRET_KEY="receipt-test")
        app.init_db()
        with app.get_db() as conn:
            conn.execute("INSERT INTO suppliers (code,name,created_at,updated_at) VALUES ('S1','供应商甲',?,?)", (NOW, NOW))
            conn.execute("INSERT INTO suppliers (code,name,created_at,updated_at) VALUES ('S2','供应商乙',?,?)", (NOW, NOW))
            conn.execute("INSERT INTO warehouse_locations (code,name,enabled,created_at,updated_at) VALUES ('RAW-A','原料区',1,?,?)", (NOW, NOW))
            conn.execute("INSERT INTO warehouse_locations (code,name,enabled,created_at,updated_at) VALUES ('DISABLED','停用仓',0,?,?)", (NOW, NOW))
            self.location_id = conn.execute("SELECT id FROM warehouse_locations WHERE code='RAW-A'").fetchone()[0]
            for name, receive, price in (("receiver", 1, 1), ("blind", 1, 0), ("reader", 0, 0)):
                conn.execute("INSERT INTO users (username,password_hash,role,can_view_purchases,can_receive_purchases,can_view_purchase_prices,created_at,updated_at) VALUES (?,'hash','operator',1,?,?,?,?)", (name, receive, price, NOW, NOW))
        self.client = app.app.test_client()
        self.login("receiver")
        self.order_id, self.item_id = self.create_order()
        self.url = f"/admin/purchase-receipts/raw-material/{self.order_id}/new"

    def tearDown(self):
        app.DB_PATH, app.DATABASE_READY, testing, secret = self.original
        app.app.config.update(TESTING=testing, SECRET_KEY=secret)
        self.tmp.cleanup()

    def login(self, name):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(admin_logged_in=True, admin_username=name)

    def scalar(self, sql, *params):
        with app.get_db() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def create_order(self, category="raw_material", supplier_id="1", order_no="PO-RAW"):
        data = dict(category=category, supplier_id=supplier_id, purchased_at="2026-09-12", status="ordered",
                    delivery_address="厂区", recipient="收货人", recipient_phone="123", rows=[dict(
                        item_name="镀锌板", drawing_no="D1", material="AB", length="1300", width="1250", height="30",
                        thickness="1.5", quantity="100", unit_price="9876.54", expected_at="2026-09-20")])
        with app.get_db() as conn:
            oid = p.create_purchase_order(conn, p.normalize_purchase_order_payload(data, can_view_prices=True), "buyer", NOW)
            conn.execute("UPDATE purchase_orders SET order_no=? WHERE id=?", (order_no, oid))
            return oid, conn.execute("SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (oid,)).fetchone()[0]

    def page_data(self, url=None):
        response = self.client.get(url or self.url)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'<script[^>]*data-receipt-data[^>]*>(.*?)</script>', response.text, re.S)
        self.assertIsNotNone(match)
        return json.loads(match[1]), response.text

    def payload(self, actual="40", qualified="30", key="test-key"):
        data, _ = self.page_data()
        return dict(preview_token=data["preview_token"], csrf_token=data["csrf_token"], idempotency_key=key,
                    received_at="2026-09-12", remark="首批", rows=[dict(purchase_order_item_id=self.item_id,
                    item_name="实际镀锌板", material="AB", length="1290", width="1250", thickness="1.4",
                    actual_quantity=actual, qualified_quantity=qualified, location_id=str(self.location_id),
                    invoice_status="pending", remark="待处理")])

    def test_search_preserves_category_and_filters_supplier_order_keyword(self):
        self.create_order("carton", order_no="PO-CARTON")
        self.create_order(supplier_id="2", order_no="PO-OTHER-SUPPLIER")
        response = self.client.get("/admin/purchase-receipts/raw-material", query_string=dict(supplier_id="1", order_no="PO-RAW", q="镀锌"))
        self.assertEqual(response.status_code, 200)
        for expected in ("PO-RAW", "供应商甲", "2026-09-12", "镀锌", "订购数量", "累计到货", "剩余数量", 'href="/admin/purchase-receipts/raw-material"'):
            self.assertIn(expected, response.text)
        self.assertNotIn("PO-CARTON", response.text)
        self.assertNotIn("PO-OTHER-SUPPLIER", response.text)

    def test_category_dimensions_and_enabled_locations(self):
        for category, visible, hidden in (("raw_material", "thickness", "height"), ("carton", "height", "thickness"), ("outsourcing", "drawing_no", None), ("other", "item_name", None)):
            oid = self.order_id if category == "raw_material" else self.create_order(category, order_no=category)[0]
            _, html = self.page_data(f"/admin/purchase-receipts/{category.replace('_', '-')}/{oid}/new")
            self.assertIn(f'data-field="{visible}"', html)
            if hidden:
                self.assertNotIn(f'data-field="{hidden}"', html)
            self.assertIn("原料区", html)
            self.assertNotIn("停用仓", html)

    def test_post_permission_and_csrf_prevent_writes(self):
        payload = self.payload()
        self.assertEqual(self.client.post(self.url, json=dict(payload, csrf_token="bad")).status_code, 403)
        self.login("reader")
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)

    def test_receipt_only_user_can_discover_receiving_without_unrelated_access(self):
        with app.get_db() as conn:
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")
                       if row["name"].startswith("can_") and row["name"] != "can_receive_purchases"]
            conn.execute("UPDATE users SET " + ",".join(f"{column}=0" for column in columns)
                         + " WHERE username='receiver'")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        navigation = response.text.split("<nav>", 1)[1].split("</nav>", 1)[0]
        dashboard = response.text.split('<section class="module-grid">', 1)[1].split("</section>", 1)[0]
        receipt_link = 'href="/admin/purchase-receipts/raw-material"'
        self.assertIn(receipt_link, navigation)
        self.assertIn(receipt_link, dashboard)
        self.assertIn("采购订单入库", dashboard)
        for html in (navigation, dashboard):
            self.assertNotIn('href="/admin/purchases/raw-material"', html)
            self.assertNotIn('href="/admin/arrival-records"', html)
        receipt_page = self.client.get("/admin/purchase-receipts/raw-material")
        self.assertEqual(receipt_page.status_code, 200)
        receipt_navigation = receipt_page.text.split("<nav>", 1)[1].split("</nav>", 1)[0]
        self.assertRegex(receipt_navigation, r'class="[^"]*active-nav-link[^"]*" href="/admin/purchase-receipts/raw-material"')
        self.assertEqual(self.client.get("/admin/purchases/raw-material").status_code, 302)
        self.assertEqual(self.client.get("/admin/arrival-records").status_code, 302)
        self.assertEqual(self.scalar("SELECT can_view_purchases + can_manage_purchases + can_manage_carton_purchases FROM users WHERE username='receiver'"), 0)
        self.login("reader")
        self.assertNotIn(receipt_link, self.client.get("/admin").text)

    def test_signed_token_cannot_be_tampered_or_used_for_another_order(self):
        payload = self.payload()
        self.assertEqual(self.client.post(self.url, json=dict(payload, preview_token="forged")).status_code, 409)
        oid, _ = self.create_order(order_no="PO-SECOND")
        self.assertEqual(self.client.post(f"/admin/purchase-receipts/raw-material/{oid}/new", json=payload).status_code, 403)
        self.assertEqual(self.client.get(f"/admin/purchase-receipts/carton/{self.order_id}/new").status_code, 404)
        self.assertEqual(self.client.get("/admin/purchase-receipts/nonsense").status_code, 404)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)

    def test_receipt_commit_duplicate_and_changed_retry(self):
        payload = self.payload()
        first = self.client.post(self.url, json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertFalse(first.json["duplicate"])
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 30)
        self.assertEqual(self.scalar("SELECT status FROM purchase_orders WHERE id=?", self.order_id), "partially_received")
        second = self.client.post(self.url, json=payload)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json["duplicate"])
        self.assertEqual(first.json["id"], second.json["id"])
        payload["remark"] = "changed"
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 409)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions"), 1)

    def test_over_receipt_and_stale_conflicts_return_current_and_redact_prices(self):
        self.login("blind")
        payload = self.payload(actual="101")
        response = self.client.post(self.url, json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json["needs_confirmation"])
        self.assertEqual(response.json["current"]["rows"][0]["remaining_quantity"], 100)
        for secret in ("unit_price", "line_total", "987654", "9876.54"):
            self.assertNotIn(secret, response.text)
        old = self.payload(actual="10", qualified="10", key="stale")
        payload["confirm_over_receipt"] = True
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 201)
        stale = self.client.post(self.url, json=old)
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(stale.json["needs_confirmation"])
        self.assertEqual(stale.json["current"]["rows"][0]["actual_quantity"], 101)
        self.assertTrue(stale.json["preview_token"])
        self.assertNotIn("unit_price", stale.text)

    def test_whitespace_normalized_retry_is_reported_as_duplicate(self):
        payload = self.payload(key="  normalized-key  ")
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 201)
        retry = self.client.post(self.url, json=payload)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json["duplicate"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 1)

    def test_linked_lot_targets_category_inventory_including_zero(self):
        result = self.client.post(self.url, json=self.payload()).json
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_view_purchase_inventory=1 WHERE username='receiver'")
        lot_no = self.scalar("SELECT lot_no FROM purchase_inventory_lots")
        self.assertIn(f'href="/admin/purchase-inventory/raw-material?q={lot_no}&amp;include_zero=1"',
                      self.client.get(result["redirect_url"]).text)

    def test_detail_actual_differences_lot_and_void_commit(self):
        result = self.client.post(self.url, json=self.payload()).json
        detail = self.client.get(result["redirect_url"])
        self.assertEqual(detail.status_code, 200)
        for text in ("实际镀锌板", "1290", "1300", "原料区", "待开票", "receiver", "PIL-", "作废到货单"):
            self.assertIn(text, detail.text)
        void_url = result["redirect_url"] + "/void"
        self.assertEqual(self.client.post(void_url, json={}).status_code, 403)
        with self.client.session_transaction() as session:
            csrf = session["inventory_csrf_token"]
        self.assertEqual(self.client.post(void_url, json=dict(csrf_token=csrf)).status_code, 200)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 0)
        self.assertEqual(self.scalar("SELECT status FROM purchase_orders WHERE id=?", self.order_id), "ordered")
        self.assertNotIn('data-void-receipt', self.client.get(result["redirect_url"]).text)

    def test_detail_hides_void_after_quantity_operation_and_rejects_direct_void(self):
        result = self.client.post(self.url, json=self.payload()).json
        with app.get_db() as conn:
            conn.execute("UPDATE purchase_inventory_lots SET available_quantity=29")
        self.assertNotIn('data-void-receipt', self.client.get(result["redirect_url"]).text)
        with self.client.session_transaction() as session:
            csrf = session["inventory_csrf_token"]
        self.assertEqual(self.client.post(result["redirect_url"] + "/void", json=dict(csrf_token=csrf)).status_code, 409)

    def test_price_blind_html_and_decoded_token_never_expose_prices(self):
        self.login("blind")
        data, html = self.page_data()
        signer = app.URLSafeTimedSerializer(app.app.secret_key, salt="purchase-receipt-preview-v1")
        decoded = json.dumps(signer.loads(data["preview_token"]))
        result = self.client.post(self.url, json=self.payload()).json
        detail = self.client.get(result["redirect_url"]).text
        for rendered in (html, decoded, detail):
            for secret in ("unit_price", "line_total", "987654", "9876.54", "单价"):
                self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
