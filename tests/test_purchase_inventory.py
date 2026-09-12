import json
import unittest

import procurement_inventory as pi
from tests import test_purchase_receipts as fixtures


class PurchaseInventoryTests(unittest.TestCase):
    setUp_receipt = fixtures.PurchaseReceiptTests.setUp
    tearDown = fixtures.PurchaseReceiptTests.tearDown
    create_order = fixtures.PurchaseReceiptTests.create_order
    scalar = fixtures.PurchaseReceiptTests.scalar
    call = fixtures.PurchaseReceiptTests.call
    preview = fixtures.PurchaseReceiptTests.preview
    payload = fixtures.PurchaseReceiptTests.payload
    post = fixtures.PurchaseReceiptTests.post

    def setUp(self):
        self.setUp_receipt()
        self.conn.execute("UPDATE purchase_orders SET order_no='PO-RAW' WHERE id=?", (self.order_id,))
        self.post(self.payload(qualified="100"))
        self.lot_id = self.scalar("SELECT id FROM purchase_inventory_lots")
        self.conn.execute("INSERT INTO warehouse_locations VALUES (2,'RAW-B','备用区',1),(3,'OLD','停用',0)")
        self.conn.commit()

    def lot(self, lot_id=None):
        return dict(self.conn.execute("SELECT * FROM purchase_inventory_lots WHERE id=?", (lot_id or self.lot_id,)).fetchone())

    def transfer(self, **changes):
        args = dict(quantity=20, target_location_id=2, reason="换库位", actor="keeper", now=self.now,
                    expected_version=1, idempotency_key="transfer-1")
        args.update(changes)
        return self.call("transfer_purchase_inventory", self.conn, self.lot_id, **args)

    def adjust(self, **changes):
        args = dict(counted_quantity=90, reason="少10张", actor="keeper", now=self.now,
                    expected_version=1, idempotency_key="adjust-1")
        args.update(changes)
        return self.call("adjust_purchase_inventory", self.conn, self.lot_id, **args)

    def query(self, **filters):
        return self.call("fetch_purchase_inventory", self.conn, dict(category="raw_material", **filters), include_prices=False)

    def test_filters_and_actual_projection_hide_financial_fields(self):
        rows = self.query(supplier_id="1", order_no="PO-RAW", q="DC51D", location_id="1",
                          received_from="2026-09-12", received_to="2026-09-12", invoice_status="pending")
        self.assertEqual([r["id"] for r in rows], [self.lot_id])
        self.assertEqual((rows[0]["item_name"], rows[0]["length"], rows[0]["location_code"]), ("实际镀锌板", 1290, "RAW-A"))
        self.assertEqual(rows[0]["opening_quantity"], 100)
        for field in ("unit_price_minor", "amount_minor", "currency"):
            self.assertNotIn(field, rows[0])
        for filters in (dict(supplier_id="2"), dict(order_no="WRONG"), dict(q="ABSENT"), dict(location_id="2"),
                        dict(received_from="2026-09-13"), dict(received_to="2026-09-11"), dict(invoice_status="invoiced"),
                        dict(receipt_no="MISSING")):
            self.assertEqual(self.query(**filters), [])
        receipt_no = self.scalar("SELECT receipt_no FROM purchase_receipts")
        self.assertEqual(len(self.query(receipt_no=receipt_no, q="实际镀锌板")), 1)
        priced = self.call("fetch_purchase_inventory", self.conn, {"category": "raw_material"}, include_prices=True)[0]
        self.assertEqual((priced["unit_price_minor"], priced["amount_minor"]), (325, 32500))

    def test_lot_number_query_is_exact_and_zero_stock_is_opt_in(self):
        number = self.lot()["lot_no"]
        self.assertEqual(len(self.query(q=number)), 1)
        self.assertEqual(self.query(q=number[:-1]), [])
        self.adjust(counted_quantity=0)
        self.assertEqual(self.query(q=number), [])
        self.assertEqual(len(self.query(q=number, include_zero="1")), 1)
        self.assertEqual(self.call("fetch_purchase_inventory", self.conn, {"category": "carton", "include_zero": "1"}, False), [])

    def test_partial_transfer_copies_snapshots_and_pairs_conserving_transactions(self):
        original = self.lot()
        result = self.transfer()
        target = self.lot(result["target_lot_id"])
        self.assertEqual((self.lot()["available_quantity"], target["available_quantity"]), (80, 20))
        self.assertEqual(self.lot()["version"], 2)
        self.assertEqual((target["source_lot_id"], target["source_kind"], target["location_id"]), (self.lot_id, "transfer", 2))
        for field in (*pi.ACTUAL_FIELDS, "origin_receipt_item_id", "supplier_name", "unit_price_minor", "invoice_status", "receipt_id"):
            self.assertEqual(target[field], original[field])
        tx = self.conn.execute("SELECT * FROM purchase_inventory_transactions WHERE related_type='operation' AND related_id=? ORDER BY id", (result["transfer_id"],)).fetchall()
        self.assertEqual([r["quantity_delta"] for r in tx], [-20, 20])
        self.assertEqual([r["paired_transaction_id"] for r in tx], [tx[1]["id"], tx[0]["id"]])
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])
        self.assertFalse(pi.receipt_voidability(self.conn, original["receipt_id"])[0])

    def test_full_transfer_does_not_rewrite_original_location_or_opening(self):
        self.transfer(quantity=100)
        self.assertEqual((self.lot()["available_quantity"], self.lot()["location_id"], self.lot()["opening_quantity"]), (0, 1, 100))

    def test_invalid_transfer_rolls_back_and_stale_returns_price_safe_current(self):
        for changes in (dict(quantity=0), dict(quantity=-1), dict(quantity="1.5"), dict(quantity=True), dict(quantity=101),
                        dict(target_location_id=1), dict(target_location_id=3), dict(target_location_id=999),
                        dict(reason=" "), dict(expected_version=9), dict(idempotency_key="")):
            with self.subTest(changes=changes), self.assertRaises(pi.PurchaseInventoryConflict):
                self.transfer(**changes)
        self.assertEqual(self.lot()["available_quantity"], 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 0)
        with self.assertRaises(pi.PurchaseInventoryConflict) as error:
            self.transfer(expected_version=9)
        self.assertEqual(error.exception.current["available_quantity"], 100)
        self.assertNotIn("unit_price_minor", error.exception.current)

    def test_disabled_source_rejects_quantity_mutations(self):
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        for command in (self.transfer, self.adjust):
            with self.assertRaises(pi.PurchaseInventoryConflict):
                command()

    def test_adjustment_is_compensating_audited_and_can_reach_zero(self):
        result = self.adjust(counted_quantity=0)
        self.assertEqual((self.lot()["available_quantity"], self.lot()["opening_quantity"], self.lot()["version"]), (0, 100, 2))
        tx = self.conn.execute("SELECT * FROM purchase_inventory_transactions WHERE transaction_type='adjustment'").fetchone()
        self.assertEqual(tx["quantity_delta"], -100)
        self.assertEqual(tx["remark"], "盘点调整：100 -> 0；原因：少10张")
        self.assertEqual((tx["related_id"], tx["operator"]), (result["operation_id"], "keeper"))
        self.adjust(counted_quantity=2, expected_version=2, idempotency_key="gain")
        self.assertEqual(self.lot()["available_quantity"], 2)
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_adjustment_rejects_noop_negative_stale_and_blank_reason(self):
        for changes in (dict(counted_quantity=100), dict(counted_quantity=-1), dict(counted_quantity="0.5"),
                        dict(reason=""), dict(expected_version=99)):
            with self.assertRaises(pi.PurchaseInventoryConflict):
                self.adjust(**changes)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 0)

    def test_retries_return_saved_result_after_later_mutations_and_changed_input_conflicts(self):
        result = self.transfer()
        self.adjust(expected_version=2)
        self.assertEqual(self.transfer(), result)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 2)
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.transfer(quantity=21)
        adjustment = self.adjust(expected_version=2)
        self.assertEqual(adjustment["available_quantity"], 90)
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.adjust(expected_version=2, reason="changed")

    def test_invoice_updates_audit_only_and_retries_remain_stable(self):
        before = self.lot()
        args = (self.conn, self.lot_id, "invoiced", "已收到发票", "receiver", self.now, "invoice-1")
        result = self.call("update_purchase_invoice_status", *args, expected_version=1)
        self.assertEqual(self.lot()["invoice_status"], "invoiced")
        self.assertEqual(self.lot()["available_quantity"], before["available_quantity"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_transactions"), 1)
        event = self.conn.execute("SELECT * FROM purchase_inventory_invoice_events").fetchone()
        self.assertEqual((event["old_status"], event["new_status"], event["operator"]), ("pending", "invoiced", "receiver"))
        self.call("update_purchase_invoice_status", self.conn, self.lot_id, "not_required", "更正", "receiver", self.now, "invoice-2", expected_version=2)
        self.assertEqual(self.call("update_purchase_invoice_status", *args, expected_version=1), result)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_invoice_events"), 2)
        for status, key in (("invalid", "invalid"), ("pending", "invoice-1"), ("not_required", "noop")):
            with self.assertRaises(pi.PurchaseInventoryConflict):
                self.call("update_purchase_invoice_status", self.conn, self.lot_id, status, "", "receiver", self.now, key, expected_version=3)

    def test_failure_mid_transfer_rolls_back_all_stock_and_audit_changes(self):
        self.conn.execute("CREATE TRIGGER fail_transfer BEFORE INSERT ON purchase_inventory_transactions WHEN NEW.transaction_type='transfer_in' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.transfer()
        self.assertEqual(self.lot()["available_quantity"], 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_lots"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 0)

    def test_operation_saved_result_migrates_existing_schema_idempotently(self):
        if "result_json" in {r["name"] for r in self.conn.execute("PRAGMA table_info(purchase_inventory_operations)")}:
            self.conn.execute("ALTER TABLE purchase_inventory_operations DROP COLUMN result_json")
        pi.ensure_purchase_inventory_tables(self.conn)
        pi.ensure_purchase_inventory_tables(self.conn)
        self.assertIn("result_json", {r["name"] for r in self.conn.execute("PRAGMA table_info(purchase_inventory_operations)")})
        result = self.transfer()
        saved = self.scalar("SELECT result_json FROM purchase_inventory_operations")
        self.assertEqual(json.loads(saved), result)

    def test_voided_receipt_cannot_be_revived_by_positive_count(self):
        pi.void_purchase_receipt(self.conn, self.lot()["receipt_id"], "receiver", self.now)
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.adjust(counted_quantity=5, expected_version=2)
        self.assertEqual(self.lot()["available_quantity"], 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_operations"), 0)
