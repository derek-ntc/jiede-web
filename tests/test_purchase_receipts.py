import copy
import sqlite3
import unittest

import procurement as p
import procurement_inventory as pi


class PurchaseReceiptTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        p.ensure_procurement_tables(self.conn)
        self.conn.execute("CREATE TABLE warehouse_locations (id INTEGER PRIMARY KEY, code TEXT, name TEXT, enabled INTEGER)")
        self.conn.execute("INSERT INTO warehouse_locations VALUES (1,'RAW-A','原料区',1)")
        pi.ensure_purchase_inventory_tables(self.conn)
        self.now = "2026-09-12T10:00:00"
        self.location_id = 1
        self.conn.execute("INSERT INTO suppliers (code,name,created_at,updated_at) VALUES ('S1','供应商甲',?,?)", (self.now, self.now))
        self.order_id, self.item_id = self.create_order()
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def call(self, name, *args, **kwargs):
        service = getattr(pi, name, None)
        self.assertTrue(callable(service), f"Missing receipt service: {name}")
        return service(*args, **kwargs)

    def scalar(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def create_order(self, category="raw_material", quantity="100", rows=None):
        data = dict(category=category, supplier_id="1", purchased_at="2026-09-12", status="ordered",
                    delivery_address="厂区", recipient="收货人", recipient_phone="123",
                    rows=rows or [dict(item_name="镀锌板", material="DC51D+Z", length="1300", width="1250",
                                      thickness="1.5", quantity=quantity, unit_price="3.25", expected_at="2026-09-20")])
        oid = p.create_purchase_order(self.conn, p.normalize_purchase_order_payload(data, can_view_prices=True), "buyer", self.now)
        return oid, self.scalar("SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id", oid)

    def preview(self, oid=None):
        return self.call("load_receipt_preview", self.conn, oid or self.order_id)

    def payload(self, *, key="receipt-1", actual="100", qualified="98", oid=None, item_id=None):
        return dict(preview_token=self.call("receipt_preview_token", self.preview(oid)),
                    idempotency_key=key, received_at="2026-09-12", remark="首批",
                    rows=[dict(purchase_order_item_id=item_id or self.item_id, item_name="实际镀锌板", material="DC51D+Z",
                               length="1290", width="1250", thickness="1.4", actual_quantity=actual,
                               qualified_quantity=qualified, location_id=str(self.location_id),
                               invoice_status="pending", remark="两张待处理")])

    def post(self, payload=None):
        return self.call("post_purchase_receipt", self.conn, payload or self.payload(), "receiver", self.now)

    def assert_conflict(self, payload, *, confirmation=False):
        with self.assertRaises(pi.PurchaseInventoryConflict) as caught:
            self.post(payload)
        self.assertEqual(caught.exception.needs_confirmation, confirmation)
        return caught.exception

    def test_actual_snapshot_creates_stock_without_changing_order_item(self):
        result = self.post()
        self.assertEqual(self.scalar("SELECT item_name FROM purchase_order_items WHERE id=?", self.item_id), "镀锌板")
        self.assertEqual(self.scalar("SELECT length FROM purchase_order_items WHERE id=?", self.item_id), 1300)
        self.assertEqual(self.scalar("SELECT item_name FROM purchase_receipt_items"), "实际镀锌板")
        lot = dict(self.conn.execute("SELECT * FROM purchase_inventory_lots").fetchone())
        self.assertEqual((lot["length"], lot["width"], lot["thickness"], lot["available_quantity"]), (1290, 1250, 1.4, 98))
        self.assertEqual((lot["supplier_name"], lot["unit_price_minor"], lot["currency"]), ("供应商甲", 325, "CNY"))
        self.assertEqual(self.scalar("SELECT quantity_delta FROM purchase_inventory_transactions"), 98)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_invalid_second_row_rolls_back_everything(self):
        oid, iid = self.create_order(rows=[dict(item_name=n, material="钢", quantity="100", expected_at="2026-09-20") for n in ("A", "B")])
        data = self.payload(oid=oid, item_id=iid)
        second = dict(data["rows"][0], purchase_order_item_id=iid + 1, location_id="999999")
        data["rows"].append(second)
        self.assert_conflict(data)
        for table in ("purchase_receipts", "purchase_receipt_items", "purchase_inventory_lots", "purchase_inventory_transactions"):
            self.assertEqual(self.scalar(f"SELECT COUNT(*) FROM {table}"), 0)
        self.assertEqual(self.scalar("SELECT status FROM purchase_orders WHERE id=?", oid), "ordered")

    def test_four_categories_and_optional_snapshots(self):
        cases = [("raw_material", dict(material="钢", length="1", width="2", thickness=".5")),
                 ("carton", dict(material="AB", length="1", width="2", height="3")),
                 ("outsourcing", dict(drawing_no="D1")), ("other", dict(drawing_no="D2"))]
        for category, fields in cases:
            with self.subTest(category=category):
                oid, iid = self.create_order(category)
                data = self.payload(oid=oid, item_id=iid, key=category)
                data["rows"][0] = dict(fields, purchase_order_item_id=iid, actual_quantity="1", qualified_quantity="1", location_id="1", invoice_status="not_required")
                result = self.post(data)
                lot = self.conn.execute("SELECT * FROM purchase_inventory_lots WHERE receipt_id=?", (result["id"],)).fetchone()
                self.assertEqual(lot["category"], category)
                self.assertEqual(lot["surface"], "")
                if category in ("other", "outsourcing"):
                    self.assertIsNone(lot["thickness"])
                if category == "carton":
                    self.assertEqual((lot["length"], lot["width"], lot["height"]), (1, 2, 3))

    def test_required_category_fields_are_validated(self):
        for category, required in (("raw_material", ("material", "length", "width", "thickness")),
                                   ("carton", ("material", "length", "width", "height"))):
            oid, iid = self.create_order(category)
            for field in required:
                data = self.payload(oid=oid, item_id=iid, key=f"{category}-{field}")
                data["rows"][0]["height"] = "10"
                data["rows"][0][field] = ""
                with self.subTest(category=category, field=field):
                    self.assert_conflict(data)
        for category in ("other", "outsourcing"):
            oid, iid = self.create_order(category)
            data = self.payload(oid=oid, item_id=iid, key=category)
            data["rows"][0]["item_name"] = ""
            self.assert_conflict(data)

    def test_invalid_quantities_dimensions_invoice_and_dates_are_rejected(self):
        for field, values in {"actual_quantity": ("0", "1.5", True, "2147483648"),
                              "qualified_quantity": ("-1", "101", "1.5", False),
                              "length": ("NaN", "Infinity", "0", "-1"),
                              "invoice_status": ("", "paid")}.items():
            for value in values:
                data = self.payload()
                data["rows"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_conflict(data)
        for key, value in (("received_at", "2026-02-30"), ("idempotency_key", ""), ("rows", [])):
            data = self.payload()
            data[key] = value
            self.assert_conflict(data)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)

    def test_zero_qualified_preserves_receipt_without_stock(self):
        self.post(self.payload(qualified="0"))
        self.assertEqual(self.scalar("SELECT qualified_quantity FROM purchase_receipt_items"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_lots"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions"), 0)
        self.assertEqual(self.scalar("SELECT status FROM purchase_orders WHERE id=?", self.order_id), "received")

    def test_partial_then_complete_uses_cumulative_actual_not_qualified(self):
        self.post(self.payload(actual="40", qualified="30"))
        preview = self.preview()
        self.assertEqual(preview["order"]["status"], "partially_received")
        self.assertEqual((preview["rows"][0]["actual_quantity"], preview["rows"][0]["qualified_quantity"], preview["rows"][0]["remaining_quantity"]), (40, 30, 60))
        self.post(self.payload(key="receipt-2", actual="60", qualified="50"))
        self.assertEqual(self.preview()["order"]["status"], "received")

    def test_complete_requires_every_order_row(self):
        oid, iid = self.create_order(rows=[dict(item_name=n, material="钢", quantity="100", expected_at="2026-09-20") for n in ("A", "B")])
        self.post(self.payload(oid=oid, item_id=iid))
        self.assertEqual(self.preview(oid)["order"]["status"], "partially_received")

    def test_stale_preview_returns_current_and_writes_nothing(self):
        data = self.payload()
        self.conn.execute("UPDATE purchase_order_items SET length=1400 WHERE id=?", (self.item_id,))
        conflict = self.assert_conflict(data)
        self.assertEqual(conflict.current["rows"][0]["length"], 1400)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)

    def test_competing_receipt_invalidates_preview(self):
        old = self.payload(actual="10", qualified="10", key="old")
        self.post(self.payload(actual="10", qualified="10"))
        self.assert_conflict(old)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 1)

    def test_preview_token_is_deterministic_and_tracks_location_changes(self):
        preview = self.preview()
        token = self.call("receipt_preview_token", preview)
        self.assertEqual(token, self.call("receipt_preview_token", copy.deepcopy(preview)))
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        self.assertNotEqual(token, self.call("receipt_preview_token", self.preview()))

    def test_disabled_location_rejected_even_with_fresh_preview(self):
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        self.assert_conflict(self.payload())

    def test_matching_retry_returns_existing_without_new_stock(self):
        data = self.payload()
        first = self.post(data)
        second = self.post(data)
        self.assertEqual(first, second)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions"), 1)

    def test_changed_retry_is_rejected_before_stale_preview(self):
        data = self.payload()
        self.post(data)
        data["rows"][0]["remark"] = "changed"
        conflict = self.assert_conflict(data)
        self.assertIn("重复", str(conflict))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 1)

    def test_over_receipt_requires_literal_true_and_can_complete(self):
        for value in (None, "true", 1):
            data = self.payload(actual="101")
            data["confirm_over_receipt"] = value
            self.assert_conflict(data, confirmation=True)
        data["confirm_over_receipt"] = True
        self.assertEqual(self.post(data)["status"], "posted")
        self.assertEqual(self.preview()["rows"][0]["remaining_quantity"], 0)

    def test_cross_order_category_and_duplicate_rows_are_rejected(self):
        oid, iid = self.create_order("carton")
        for variant in ("cross_order", "category", "order_id", "duplicate"):
            data = self.payload()
            if variant == "cross_order":
                data["rows"].append(dict(data["rows"][0], purchase_order_item_id=iid))
            elif variant == "category":
                data["category"] = "carton"
            elif variant == "order_id":
                data["purchase_order_id"] = oid
            else:
                data["rows"].append(dict(data["rows"][0]))
            with self.subTest(variant=variant):
                self.assert_conflict(data)

    def test_cancelled_and_draft_orders_cannot_receive(self):
        for status in ("cancelled", "draft"):
            self.conn.execute("UPDATE purchase_orders SET status=? WHERE id=?", (status, self.order_id))
            self.assert_conflict(self.payload())

    def test_receivable_listing_is_filtered_and_excludes_closed_orders(self):
        other_id, _ = self.create_order("other")
        self.conn.execute("UPDATE purchase_orders SET status='draft' WHERE id=?", (other_id,))
        self.assertEqual([r["id"] for r in self.call("load_receivable_orders", self.conn, "raw_material", {})], [self.order_id])
        self.assertEqual(self.call("load_receivable_orders", self.conn, "other", {}), [])
        self.assertEqual(self.call("load_receivable_orders", self.conn, "raw_material", {"q": "not-there"}), [])
        self.post()
        self.assertEqual(self.call("load_receivable_orders", self.conn, "raw_material", {}), [])

    def test_void_reverses_stock_and_actual_totals_once(self):
        result = self.post()
        self.assertEqual(self.call("receipt_voidability", self.conn, result["id"]), (True, ""))
        voided = self.call("void_purchase_receipt", self.conn, result["id"], "manager", self.now)
        self.assertEqual(voided["status"], "voided")
        self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 0)
        self.assertEqual(self.scalar("SELECT quantity_delta FROM purchase_inventory_transactions WHERE transaction_type='reversal'"), -98)
        self.assertEqual(self.preview()["order"]["status"], "ordered")
        self.assertEqual(self.preview()["rows"][0]["actual_quantity"], 0)
        again = self.call("void_purchase_receipt", self.conn, result["id"], "other", "later")
        self.assertEqual(again, voided)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions"), 2)
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_zero_stock_receipt_can_be_voided(self):
        result = self.post(self.payload(qualified="0"))
        self.call("void_purchase_receipt", self.conn, result["id"], "manager", self.now)
        self.assertEqual(self.preview()["order"]["status"], "ordered")

    def test_void_keeps_other_receipts_and_cancelled_order_status(self):
        first = self.post(self.payload(actual="10", qualified="10"))
        self.post(self.payload(key="second", actual="10", qualified="10"))
        self.call("void_purchase_receipt", self.conn, first["id"], "manager", self.now)
        self.assertEqual(self.preview()["order"]["status"], "partially_received")
        p.cancel_purchase_order(self.conn, self.order_id, "buyer", self.now)
        second = self.scalar("SELECT id FROM purchase_receipts WHERE idempotency_key='second'")
        self.call("void_purchase_receipt", self.conn, second, "manager", self.now)
        self.assertEqual(self.preview()["order"]["status"], "cancelled")

    def test_any_quantity_operation_blocks_void_even_if_balance_restored(self):
        for kind in ("adjustment", "transfer_out", "transfer_in", "outbound"):
            with self.subTest(kind=kind):
                result = self.post(self.payload(key=kind, actual="1", qualified="1"))
                lot_id = self.scalar("SELECT id FROM purchase_inventory_lots WHERE receipt_id=?", result["id"])
                self.conn.execute("INSERT INTO purchase_inventory_transactions (transaction_no,lot_id,transaction_type,quantity_delta,operator,created_at) VALUES (?,?,?,1,'operator','now')", (kind, lot_id, kind))
                allowed, reason = self.call("receipt_voidability", self.conn, result["id"])
                self.assertFalse(allowed)
                self.assertTrue(reason)
                with self.assertRaises(pi.PurchaseInventoryConflict):
                    self.call("void_purchase_receipt", self.conn, result["id"], "manager", self.now)
                self.assertEqual(self.scalar("SELECT status FROM purchase_receipts WHERE id=?", result["id"]), "posted")

    def test_descendant_transfer_blocks_void_without_transactions(self):
        result = self.post()
        lot = dict(self.conn.execute("SELECT * FROM purchase_inventory_lots").fetchone())
        source_id = lot.pop("id")
        lot.update(lot_no="transfer-child", source_kind="transfer", source_lot_id=source_id, opening_quantity=1, available_quantity=0)
        self.conn.execute(f"INSERT INTO purchase_inventory_lots ({','.join(lot)}) VALUES ({','.join('?' for _ in lot)})", tuple(lot.values()))
        self.assertFalse(self.call("receipt_voidability", self.conn, result["id"])[0])
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.call("void_purchase_receipt", self.conn, result["id"], "manager", self.now)

    def test_changed_balance_blocks_void_without_transactions(self):
        result = self.post()
        self.conn.execute("UPDATE purchase_inventory_lots SET available_quantity=97")
        self.assertFalse(self.call("receipt_voidability", self.conn, result["id"])[0])

    def test_database_failure_rolls_back_inserted_receipt_and_lot(self):
        data = self.payload()
        self.conn.execute("CREATE TRIGGER reject_receipt_tx BEFORE INSERT ON purchase_inventory_transactions BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises((sqlite3.IntegrityError, pi.PurchaseInventoryConflict)):
            self.post(data)
        for table in ("purchase_receipts", "purchase_receipt_items", "purchase_inventory_lots"):
            self.assertEqual(self.scalar(f"SELECT COUNT(*) FROM {table}"), 0)
        self.assertEqual(self.preview()["order"]["status"], "ordered")


if __name__ == "__main__":
    unittest.main()
