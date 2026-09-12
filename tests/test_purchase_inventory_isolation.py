"""Real HTTP workflows must not change product, sales, or legacy history data."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zipfile import ZipFile

from openpyxl import load_workbook

import app
import procurement_inventory as pi
from tests import test_purchase_receipt_routes as fixtures
from tests.test_purchase_order_exports import pdf_pages


class PurchaseInventoryIsolationTests(unittest.TestCase):
    tearDown = fixtures.PurchaseReceiptRouteTests.tearDown
    create_order = fixtures.PurchaseReceiptRouteTests.create_order
    login = fixtures.PurchaseReceiptRouteTests.login
    scalar = fixtures.PurchaseReceiptRouteTests.scalar
    page_data = fixtures.PurchaseReceiptRouteTests.page_data

    def setUp(self):
        fixtures.PurchaseReceiptRouteTests.setUp(self)
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_view_purchase_inventory=1,can_adjust_purchase_inventory=1,"
                         "can_outbound_purchase_inventory=1 WHERE username IN ('receiver','blind')")
            conn.execute("INSERT INTO warehouse_locations (code,name,enabled,created_at,updated_at) "
                         "VALUES ('TARGET','目标区',1,?,?)", (fixtures.NOW, fixtures.NOW))
            self.target_id = conn.execute("SELECT id FROM warehouse_locations WHERE code='TARGET'").fetchone()[0]
            # Nonempty sentinels catch deletion/overwrite as well as accidental inserts.
            conn.execute("INSERT INTO manuals (product_name,model,category,version,filename,original_filename,"
                         "created_at,updated_at) VALUES ('隔离产品','P-ISO','产品','1','','',?,?)", (fixtures.NOW, fixtures.NOW))
            mid = conn.execute("SELECT id FROM manuals WHERE model='P-ISO'").fetchone()[0]
            conn.execute("INSERT INTO inventory_balances (manual_id,location_id,quantity,updated_at) VALUES (?,?,17,?)",
                         (mid, self.location_id, fixtures.NOW))
            conn.execute("INSERT INTO inventory_transactions (transaction_no,type,manual_id,quantity,to_location_id,"
                         "created_at) VALUES ('ISO-PRODUCT','inbound',?,17,?,?)", (mid, self.location_id, fixtures.NOW))
            conn.execute("INSERT INTO product_orders (manual_id,order_no,ordered_at,quantity,planned_ship_at,created_at,updated_at) "
                         "VALUES (?,'ISO-SALES','2026-09-12',17,'2026-09-20',?,?)", (mid, fixtures.NOW, fixtures.NOW))
            conn.execute("INSERT INTO customers (name,created_at,updated_at) VALUES ('隔离客户',?,?)", (fixtures.NOW, fixtures.NOW))
            cid = conn.execute("SELECT id FROM customers WHERE name='隔离客户'").fetchone()[0]
            conn.execute("INSERT INTO finance_invoices (customer_id,customer_name,currency,total_minor,created_by,updated_by,"
                         "created_at,updated_at) VALUES (?,'隔离客户','CNY',12345,'tester','tester',?,?)", (cid, fixtures.NOW, fixtures.NOW))
            conn.execute("INSERT INTO arrival_records (arrived_at,item_name,quantity,unit_price,created_at,updated_at) "
                         "VALUES ('2020-01-01','历史手工到货',8,12.5,?,?)", (fixtures.NOW, fixtures.NOW))
        # Settle existing legacy-supplier migration before measuring new workflow writes.
        app.init_db()

    def isolated_snapshot(self):
        with app.get_db() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not r[0].startswith(("purchase_inventory_", "sqlite_"))
                      and r[0] not in {"purchase_receipts", "purchase_receipt_items", "purchase_orders"}]
            result = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables}
            result["purchase_orders"] = [dict(r) for r in conn.execute("SELECT * FROM purchase_orders ORDER BY id")]
            return result

    def assert_consistent(self, before, *, receipt_order_id=None, receipt_status=None):
        current = self.isolated_snapshot()
        if receipt_order_id is not None:
            # This exception exists only at the first receipt/void boundary, never
            # across transfer, adjustment, invoice status, outbound, or retries.
            old = next(row for row in before["purchase_orders"] if row["id"] == receipt_order_id)
            changed = next(row for row in current["purchase_orders"] if row["id"] == receipt_order_id)
            self.assertEqual(changed["status"], receipt_status)
            self.assertEqual(changed["updated_by"], "receiver")
            datetime.fromisoformat(changed["updated_at"])
            for field in ("status", "updated_by", "updated_at"):
                changed[field] = old[field]
        self.assertEqual(current, before, "isolated business snapshot changed")
        with app.get_db() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(pi.purchase_inventory_invariant_errors(conn), [])
            # Independent accounting equation; opening quantity is already a receipt/transfer delta.
            for lot in conn.execute("SELECT id,available_quantity FROM purchase_inventory_lots"):
                delta = conn.execute("SELECT COALESCE(SUM(quantity_delta),0) FROM purchase_inventory_transactions "
                                     "WHERE lot_id=?", (lot["id"],)).fetchone()[0]
                self.assertEqual(lot["available_quantity"], delta)
                self.assertGreaterEqual(lot["available_quantity"], 0)

    def receive(self, category, key):
        oid, iid = self.create_order(category, order_no="ISO-" + key)
        before = self.isolated_snapshot()
        url = f"/admin/purchase-receipts/{category.replace('_', '-')}/{oid}/new"
        preview, _ = self.page_data(url)
        self.csrf = preview["csrf_token"]
        payload = dict(preview_token=preview["preview_token"], csrf_token=self.csrf, idempotency_key=key,
                       received_at="2026-09-12", rows=[dict(purchase_order_item_id=iid, item_name="实物-" + category,
                       material="实际材质", length=1290, width=1250, thickness=1.4, height=30,
                       actual_quantity=100, qualified_quantity=100, location_id=self.location_id, invoice_status="pending")])
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json
        self.assert_consistent(before, receipt_order_id=oid, receipt_status="received")
        before = self.isolated_snapshot()
        self.assertEqual(self.client.post(url, json=payload).status_code, 200)
        self.assertEqual(self.scalar("SELECT status FROM purchase_orders WHERE id=?", oid), "received")
        self.assert_consistent(before)
        lot_id = self.scalar("SELECT id FROM purchase_inventory_lots WHERE receipt_id=?", receipt["id"])
        return receipt, lot_id, before

    def change(self, lot_id, action, category, key, **values):
        response = self.client.post(f"/admin/purchase-inventory/lots/{lot_id}/{action}", json=dict(
            category=category, csrf_token=self.csrf, idempotency_key=key, reason="盘点少2件" if action == "adjust" else "移库核对",
            **values))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json

    def run_flow(self, category):
        receipt, origin, before = self.receive(category, "receive-" + category)
        moved = self.change(origin, "transfer", category, "transfer-" + category,
                            quantity=40, target_location_id=self.target_id, expected_version=1)
        target = moved["target_lot_id"]
        self.assert_consistent(before)
        self.change(target, "adjust", category, "adjust-" + category, counted_quantity=38, expected_version=1)
        self.assert_consistent(before)
        self.change(target, "invoice-status", category, "invoice-" + category, new_status="invoiced", remark="票据核对", expected_version=2)
        self.assert_consistent(before)
        version = self.scalar("SELECT version FROM purchase_inventory_lots WHERE id=?", target)
        url = f"/admin/shipping/purchase-goods/{category.replace('_', '-')}/new"
        payload = dict(category=category, csrf_token=self.csrf, idempotency_key="out-" + category,
                       outbound_at="2026-09-12", used_by="王师傅", rows=[dict(lot_id=target, expected_version=version, quantity=10)])
        posted = self.client.post(url, json=payload)
        self.assertEqual(posted.status_code, 201, posted.text)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", target), 28)
        self.assert_consistent(before)
        self.assertEqual(self.client.post(url, json=payload).status_code, 200)
        self.assert_consistent(before)
        for _ in range(2):
            self.assertEqual(self.client.post(posted.json["redirect_url"] + "/void", json={"csrf_token": self.csrf}).status_code, 200)
            self.assert_consistent(before)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", origin), 60)
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", target), 38)
        rejected = self.client.post(receipt["redirect_url"] + "/void", json={"csrf_token": self.csrf})
        self.assertEqual(rejected.status_code, 409)
        self.assert_consistent(before)
        return receipt, posted.json

    def test_receipt_transfer_adjust_outbound_void_preserve_every_other_domain_for_four_categories(self):
        for category in ("raw_material", "carton", "outsourcing", "other"):
            with self.subTest(category=category):
                self.run_flow(category)

    def test_isolation_guard_detects_unrelated_order_status_and_audit_changes_during_receipt(self):
        # A real receipt side effect on an unrelated order must fail the workflow guard.
        for field, value in (("status", "cancelled"), ("updated_by", "unexpected"), ("updated_at", "2000-01-01")):
            with self.subTest(field=field):
                with app.get_db() as conn:
                    original = conn.execute(f"SELECT {field} FROM purchase_orders WHERE id=?", (self.order_id,)).fetchone()[0]
                    conn.execute(f"CREATE TRIGGER isolation_fault AFTER INSERT ON purchase_receipts BEGIN "
                                 f"UPDATE purchase_orders SET {field}='{value}' WHERE id={self.order_id}; END")
                try:
                    with self.assertRaisesRegex(AssertionError, "isolated business snapshot changed"):
                        self.receive("raw_material", "unrelated-fault-" + field)
                finally:
                    with app.get_db() as conn:
                        conn.execute("DROP TRIGGER isolation_fault")
                        conn.execute(f"UPDATE purchase_orders SET {field}=? WHERE id=?", (original, self.order_id))

    def test_isolation_guard_detects_source_order_status_and_audit_changes_during_stock_operations(self):
        receipt, lot_id, before = self.receive("raw_material", "stock-fault")
        oid = self.scalar("SELECT purchase_order_id FROM purchase_receipts WHERE id=?", receipt["id"])
        with app.get_db() as conn:
            conn.execute(f"CREATE TRIGGER isolation_fault AFTER INSERT ON purchase_inventory_transactions "
                         f"WHEN NEW.transaction_type='transfer_out' BEGIN UPDATE purchase_orders "
                         f"SET status='cancelled',updated_by='unexpected' WHERE id={oid}; END")
        self.change(lot_id, "transfer", "raw_material", "fault-transfer", quantity=40,
                    target_location_id=self.target_id, expected_version=1)
        with self.assertRaisesRegex(AssertionError, "isolated business snapshot changed"):
            self.assert_consistent(before)

    def test_unused_receipt_void_reverses_stock_once_without_rewriting_order_or_legacy_history(self):
        for category in ("raw_material", "carton", "outsourcing", "other"):
            with self.subTest(category=category):
                receipt, lot_id, before = self.receive(category, "void-" + category)
                oid = self.scalar("SELECT purchase_order_id FROM purchase_receipts WHERE id=?", receipt["id"])
                for attempt in range(2):
                    result = self.client.post(receipt["redirect_url"] + "/void", json={"csrf_token": self.csrf})
                    self.assertEqual(result.status_code, 200, result.text)
                    if attempt == 0:
                        self.assert_consistent(before, receipt_order_id=oid, receipt_status="ordered")
                    else:
                        self.assert_consistent(before)
                    before = self.isolated_snapshot()
                self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots WHERE id=?", lot_id), 0)
                self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions WHERE lot_id=?", lot_id), 2)

    def test_four_category_documents_remain_price_free_through_real_permission_routes(self):
        for category in ("raw_material", "carton", "outsourcing", "other"):
            with self.subTest(category=category):
                self.login("receiver")
                receipt, outbound = self.run_flow(category)
                self.login("blind")
                roots = [receipt["redirect_url"], outbound["redirect_url"],
                         "/admin/purchase-inventory/" + category.replace("_", "-")]
                before = self.isolated_snapshot()
                for root in roots:
                    page = self.client.get(root)
                    self.assertEqual(page.status_code, 200)
                    texts = [page.text]
                    for fmt in ("xlsx", "pdf"):
                        response = self.client.get(root + "/export." + fmt)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn("no-store", response.headers["Cache-Control"])
                        if fmt == "xlsx":
                            stream = BytesIO(response.data)
                            with ZipFile(stream) as archive:
                                texts.append(b"".join(archive.read(n) for n in archive.namelist()).decode())
                            ws = load_workbook(stream).active
                            self.assertEqual((ws.page_setup.orientation, str(ws.page_setup.paperSize)), ("landscape", "9"))
                        else:
                            texts.append("".join(pdf_pages(BytesIO(response.data))))
                    for text in texts:
                        for forbidden in ("单价", "金额", "unit_price", "amount_minor", "987654", "9876.54", "9,876.54"):
                            self.assertNotIn(forbidden, text)
                self.assert_consistent(before)

    def test_repeated_initialization_of_disposable_database_copy_preserves_full_workflow(self):
        self.run_flow("other")
        original = app.DB_PATH
        with tempfile.TemporaryDirectory(prefix="procurement-copy-") as folder:
            copied = Path(folder) / "copy.sqlite"
            with app.get_db() as source, sqlite3.connect(copied) as target:
                source.backup(target)
            try:
                app.DB_PATH = copied
                before = self.isolated_snapshot()
                with app.get_db() as conn:
                    stock_before = list(conn.iterdump())
                app.init_db()
                app.init_db()
                self.assert_consistent(before)
                with app.get_db() as conn:
                    self.assertEqual(list(conn.iterdump()), stock_before)
                self.run_flow("carton")
            finally:
                app.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
