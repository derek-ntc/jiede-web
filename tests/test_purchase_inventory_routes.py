import unittest

import app
from tests import test_purchase_receipt_routes as fixtures


class PurchaseInventoryRouteTests(unittest.TestCase):
    fixture_setup = fixtures.PurchaseReceiptRouteTests.setUp
    tearDown = fixtures.PurchaseReceiptRouteTests.tearDown
    create_order = fixtures.PurchaseReceiptRouteTests.create_order
    login = fixtures.PurchaseReceiptRouteTests.login
    scalar = fixtures.PurchaseReceiptRouteTests.scalar
    page_data = fixtures.PurchaseReceiptRouteTests.page_data
    payload = fixtures.PurchaseReceiptRouteTests.payload

    def setUp(self):
        self.fixture_setup()
        self.client.post(self.url, json=self.payload(actual="100", qualified="100"))
        self.lot_id = self.scalar("SELECT id FROM purchase_inventory_lots")
        self.lot_no = self.scalar("SELECT lot_no FROM purchase_inventory_lots")
        self.inventory_url = "/admin/purchase-inventory/raw-material"
        self.action_url = f"/admin/purchase-inventory/lots/{self.lot_id}"
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_view_purchase_inventory=1,can_adjust_purchase_inventory=1 WHERE username IN ('receiver','blind')")
            conn.execute("INSERT INTO warehouse_locations (code,name,enabled,created_at,updated_at) VALUES ('RAW-B','备用区',1,?,?)", (fixtures.NOW, fixtures.NOW))
            self.target_id = conn.execute("SELECT id FROM warehouse_locations WHERE code='RAW-B'").fetchone()[0]
        with self.client.session_transaction() as session:
            self.csrf = session["inventory_csrf_token"]

    def change_payload(self, **changes):
        data = dict(category="raw_material", quantity="20", target_location_id=self.target_id, reason="换库位",
                    expected_version=1, idempotency_key="change-1", csrf_token=self.csrf)
        data.update(changes)
        return data

    def test_category_list_filters_exact_lot_link_and_price_permissions(self):
        self.login("blind")
        result = self.client.get(self.inventory_url, query_string=dict(q=self.lot_no, include_zero="1", supplier_id="1", order_no="PO-RAW", location_id=self.location_id))
        self.assertEqual(result.status_code, 200)
        for value in (self.lot_no, "实际镀锌板", "供应商甲", "原料区", "开票状态", "当前可用数量"):
            self.assertIn(value, result.text)
        for secret in ("unit_price", "amount_minor", "987654", "9876.54", "单价"):
            self.assertNotIn(secret, result.text)
        self.assertNotIn(self.lot_no, self.client.get(self.inventory_url, query_string={"q": self.lot_no[:-1]}).text.split("<tbody>")[-1])
        self.assertNotIn(self.lot_no, self.client.get("/admin/purchase-inventory/carton").text)
        self.assertEqual(self.client.get("/admin/purchase-inventory/invalid").status_code, 404)
        self.login("receiver")
        self.assertIn("9876.54", self.client.get(self.inventory_url).text)

    def test_inventory_only_user_can_discover_list_without_product_permissions(self):
        with app.get_db() as conn:
            flags = [r["name"] for r in conn.execute("PRAGMA table_info(users)") if r["name"].startswith("can_") and r["name"] != "can_view_purchase_inventory"]
            conn.execute("UPDATE users SET " + ",".join(f"{f}=0" for f in flags) + " WHERE username='blind'")
        self.login("blind")
        dashboard = self.client.get("/admin")
        self.assertIn('href="/admin/purchase-inventory/raw-material"', dashboard.text.split("<nav>", 1)[1].split("</nav>", 1)[0])
        self.assertIn('href="/admin/purchase-inventory/raw-material"', dashboard.text.split('<section class="module-grid">', 1)[1])
        page = self.client.get(self.inventory_url)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(self.action_url + "/transfer", page.text)
        self.assertNotIn(self.action_url + "/invoice-status", page.text)
        self.assertNotIn('href="/admin/inventory"', page.text)

    def test_permissions_csrf_category_and_method_checks_prevent_mutations(self):
        for action in ("transfer", "adjust", "invoice-status"):
            payload = self.change_payload(counted_quantity=90, new_status="invoiced")
            self.assertEqual(self.client.post(self.action_url + "/" + action, json=dict(payload, csrf_token="bad")).status_code, 403)
            self.assertEqual(self.client.post(self.action_url + "/" + action, json=dict(payload, category="carton")).status_code, 409)
            self.assertEqual(self.client.post(self.action_url + "/" + action, json=dict(payload, category="")).status_code, 409)
        self.login("reader")
        self.assertEqual(self.client.get(self.inventory_url).status_code, 302)
        for action in ("transfer", "adjust", "invoice-status"):
            self.assertEqual(self.client.post(self.action_url + "/" + action, json=self.change_payload()).status_code, 302)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 0)

    def test_transfer_success_retry_and_safe_stale_conflict(self):
        payload = self.change_payload()
        first = self.client.post(self.action_url + "/transfer", json=payload)
        self.assertEqual(first.status_code, 201)
        retry = self.client.post(self.action_url + "/transfer", json=payload)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json["duplicate"])
        self.assertEqual(first.json["target_lot_id"], retry.json["target_lot_id"])
        self.login("blind")
        self.client.get(self.action_url + "/transfer")
        with self.client.session_transaction() as session:
            payload["csrf_token"] = session["inventory_csrf_token"]
        stale = self.client.post(self.action_url + "/transfer", json=dict(payload, idempotency_key="stale"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["current"]["available_quantity"], 80)
        for secret in ("unit_price", "987654", "amount_minor"):
            self.assertNotIn(secret, stale.text)

    def test_forms_keep_submitted_values_on_conflict_and_support_zero_adjustment(self):
        page = self.client.get(self.action_url + "/adjust", query_string={"category": "raw_material"})
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="expected_version" value="1"', page.text)
        invalid = self.client.post(self.action_url + "/adjust", data=self.change_payload(counted_quantity="90", expected_version=99, reason="盘点记录"))
        self.assertEqual(invalid.status_code, 409)
        self.assertIn('value="90"', invalid.text)
        self.assertIn("盘点记录", invalid.text)
        success = self.client.post(self.action_url + "/adjust", data=self.change_payload(counted_quantity="0"))
        self.assertEqual(success.status_code, 302)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", self.lot_id), 0)
        self.assertNotIn(self.lot_no, self.client.get(self.inventory_url).text)
        self.assertIn(self.lot_no, self.client.get(self.inventory_url, query_string={"include_zero": "1"}).text)

    def test_invoice_requires_receipt_permission_and_never_changes_stock(self):
        payload = self.change_payload(new_status="invoiced", remark="收票")
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_receive_purchases=0 WHERE username='receiver'")
        self.assertEqual(self.client.post(self.action_url + "/invoice-status", json=payload).status_code, 302)
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_receive_purchases=1,can_adjust_purchase_inventory=0 WHERE username='receiver'")
        self.assertEqual(self.client.post(self.action_url + "/invoice-status", json=payload).status_code, 201)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", self.lot_id), 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_invoice_events"), 1)

    def test_stock_changes_leave_every_other_domain_table_unchanged(self):
        def snapshot():
            with app.get_db() as conn:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                          if not r[0].startswith("purchase_inventory_") and not r[0].startswith("sqlite_")]
                return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                        for table in tables}
        before = snapshot()
        self.assertEqual(self.client.post(self.action_url + "/transfer", json=self.change_payload()).status_code, 201)
        self.assertEqual(self.client.post(self.action_url + "/adjust", json=self.change_payload(
            counted_quantity=78, expected_version=2, idempotency_key="count")).status_code, 201)
        self.assertEqual(self.client.post(self.action_url + "/invoice-status", json=self.change_payload(
            new_status="invoiced", idempotency_key="invoice")).status_code, 201)
        self.assertEqual(snapshot(), before)

    def test_all_category_pages_render_only_their_actual_snapshot_dimensions(self):
        import procurement_inventory as pi
        lots = {"raw_material": self.lot_no}
        for category in ("carton", "outsourcing", "other"):
            oid, iid = self.create_order(category, order_no="PO-" + category)
            with app.get_db() as conn:
                pi.post_purchase_receipt(conn, dict(category=category,
                    preview_token=pi.receipt_preview_token(pi.load_receipt_preview(conn, oid)),
                    idempotency_key=category, received_at="2026-09-12", rows=[dict(
                        purchase_order_item_id=iid, item_name=category + " actual", drawing_no="D-" + category,
                        material="AB", length=10, width=20, height=30, actual_quantity=3, qualified_quantity=3,
                        location_id=self.location_id, invoice_status="pending")]), "receiver", fixtures.NOW)
                lots[category] = conn.execute("SELECT lot_no FROM purchase_inventory_lots WHERE purchase_order_id=?", (oid,)).fetchone()[0]
        for category, number in lots.items():
            page = self.client.get("/admin/purchase-inventory/" + category.replace("_", "-"))
            self.assertEqual(page.status_code, 200)
            self.assertIn(number, page.text)
            for other, other_number in lots.items():
                if other != category:
                    self.assertNotIn(other_number, page.text)
            if category in ("raw_material", "carton"):
                self.assertIn("厚度 mm" if category == "raw_material" else "高 mm", page.text)
                self.assertNotIn("高 mm" if category == "raw_material" else "厚度 mm", page.text)
