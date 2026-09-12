import copy
import sqlite3
import tempfile
import unittest

import procurement_inventory as pi
from tests import test_purchase_receipts as fixtures


class PurchaseOutboundTests(unittest.TestCase):
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
        self.post(self.payload(actual="10", qualified="10"))
        self.post(self.payload(key="receipt-2", actual="7", qualified="7"))
        self.raw_a, self.raw_b = [r[0] for r in self.conn.execute("SELECT id FROM purchase_inventory_lots ORDER BY id")]
        self.conn.execute("UPDATE purchase_orders SET order_no='PO-RAW' WHERE id=?", (self.order_id,))
        self.conn.execute("UPDATE purchase_receipts SET order_no='PO-RAW' WHERE purchase_order_id=?", (self.order_id,))
        self.conn.execute("INSERT INTO warehouse_locations VALUES (2,'RAW-B','备用区',1)")
        self.conn.commit()

    def data(self, **changes):
        data = dict(category="raw_material", idempotency_key="out-1", outbound_at="2026-09-12",
                    used_by="王师傅", remark="一车间领用", operator="forged",
                    rows=[dict(lot_id=self.raw_a, quantity="2", expected_version=1, remark="机架"),
                          dict(lot_id=self.raw_b, quantity="3", expected_version=1, remark="面板")])
        data.update(changes)
        return data

    def outbound(self, data=None, conn=None):
        return self.call("post_purchase_outbound", conn or self.conn, data or self.data(), "keeper", self.now)

    def void(self, outbound_id, actor="supervisor"):
        return self.call("void_purchase_outbound", self.conn, outbound_id, actor, "2026-09-13T12:00:00")

    def lot(self, lot_id):
        return dict(self.conn.execute("SELECT * FROM purchase_inventory_lots WHERE id=?", (lot_id,)).fetchone())

    def state(self):
        return {table: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY id")]
                for table in ("purchase_inventory_lots", "purchase_inventory_outbounds",
                              "purchase_inventory_outbound_items", "purchase_inventory_transactions")}

    def test_multi_lot_deduction_records_caller_actor_and_negative_audit(self):
        result = self.outbound()
        self.assertEqual((self.lot(self.raw_a)["available_quantity"], self.lot(self.raw_b)["available_quantity"]), (8, 4))
        self.assertEqual((self.lot(self.raw_a)["version"], self.lot(self.raw_b)["version"]), (2, 2))
        self.assertEqual((result["status"], result["operator"], result["used_by"], result["remark"]),
                         ("posted", "keeper", "王师傅", "一车间领用"))
        tx = self.conn.execute("SELECT * FROM purchase_inventory_transactions WHERE transaction_type='outbound' ORDER BY lot_id").fetchall()
        self.assertEqual([r["quantity_delta"] for r in tx], [-2, -3])
        self.assertEqual([r["remark"] for r in tx], ["机架", "面板"])
        self.assertTrue(all(r["operator"] == "keeper" and r["related_id"] == result["id"] and r["from_location_id"] == 1 for r in tx))
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_candidate_filters_are_category_bound_positive_and_price_free(self):
        pi.adjust_purchase_inventory(self.conn, self.raw_b, 0, "清点", "keeper", self.now, 1, "zero")
        filters = dict(category="other", include_zero="1", supplier_id="1", order_no="PO-RAW", q="DC51D", location_id="1")
        rows = self.call("load_outbound_candidates", self.conn, "raw_material", filters)
        self.assertEqual([r["id"] for r in rows], [self.raw_a])
        self.assertEqual(rows[0]["version"], 1)
        self.assertFalse({"unit_price_minor", "amount_minor", "currency"} & rows[0].keys())
        for extra in (dict(supplier_id=2), dict(order_no="absent"), dict(q="absent"), dict(location_id=2)):
            self.assertEqual(self.call("load_outbound_candidates", self.conn, "raw_material", dict(filters, **extra)), [])
        self.assertEqual(self.call("load_outbound_candidates", self.conn, "carton", {}), [])
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        self.assertEqual(self.call("load_outbound_candidates", self.conn, "raw_material", {}), [])

    def test_insufficient_second_lot_rolls_back_entire_operation(self):
        data = self.data()
        data["rows"][1]["quantity"] = "999"
        before = self.state()
        with self.assertRaises(pi.PurchaseInventoryConflict) as error:
            self.outbound(data)
        self.assertEqual(self.state(), before)
        self.assertEqual(error.exception.current["available_quantity"], 7)
        self.assertNotIn("unit_price_minor", error.exception.current)

    def test_duplicate_cross_category_disabled_and_missing_lots_rejected(self):
        mutations = [lambda d: d["rows"].append(copy.deepcopy(d["rows"][0])),
                     lambda d: d.update(category="carton"),
                     lambda d: d["rows"][1].update(lot_id=999)]
        for mutate in mutations:
            data = self.data()
            mutate(data)
            before = self.state()
            with self.assertRaises(pi.PurchaseInventoryConflict):
                self.outbound(data)
            self.assertEqual(self.state(), before)
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.outbound()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 0)

    def test_header_and_row_validation_rejects_malformed_commands(self):
        for changes in (dict(category="raw"), dict(category=None), dict(used_by=" \n"), dict(outbound_at="2026-02-30"),
                        dict(outbound_at="09/12/2026"), dict(idempotency_key=""), dict(remark="x" * 4001),
                        dict(rows=[]), dict(rows=[None]), dict(rows="invalid")):
            with self.subTest(changes=changes), self.assertRaises(pi.PurchaseInventoryConflict):
                self.outbound(self.data(**changes))
        for field, values in (("quantity", (0, -1, "1.5", True, "2147483648")),
                              ("expected_version", (None, 0, True)), ("remark", ("x" * 4001,))):
            for value in values:
                data = self.data()
                data["rows"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(pi.PurchaseInventoryConflict):
                    self.outbound(data)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 0)

    def test_stale_version_returns_current_and_rolls_back_all_rows(self):
        pi.adjust_purchase_inventory(self.conn, self.raw_b, 6, "清点", "keeper", self.now, 1, "adjust")
        before = self.state()
        with self.assertRaises(pi.PurchaseInventoryConflict) as error:
            self.outbound()
        self.assertEqual((error.exception.current["available_quantity"], error.exception.current["version"]), (6, 2))
        self.assertEqual(self.state(), before)

    def test_matching_retry_ignores_row_order_and_later_stock_changes(self):
        original = self.outbound()
        pi.adjust_purchase_inventory(self.conn, self.raw_a, 7, "清点", "keeper", self.now, 2, "later")
        data = self.data()
        data["rows"].reverse()
        data["rows"][0]["quantity"] = 3
        data["used_by"] = " 王师傅 "
        self.assertEqual(self.outbound(data), original)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_outbounds"), 1)
        self.assertEqual(self.lot(self.raw_a)["available_quantity"], 7)

    def test_changed_retry_conflicts_on_each_business_field(self):
        self.outbound()
        for field, value in (("category", "other"), ("outbound_at", "2026-09-13"), ("used_by", "李师傅"), ("remark", "变更")):
            with self.subTest(field=field), self.assertRaises(pi.PurchaseInventoryConflict):
                self.outbound(self.data(**{field: value}))
        for field, value in (("quantity", 1), ("remark", "变更"), ("lot_id", 999), ("expected_version", 2)):
            data = self.data()
            data["rows"][0][field] = value
            with self.subTest(field=field), self.assertRaises(pi.PurchaseInventoryConflict):
                self.outbound(data)

    def test_snapshot_dimensions_and_master_values_survive_later_changes(self):
        result = self.outbound()
        before = dict(self.conn.execute("SELECT * FROM purchase_inventory_outbound_items WHERE outbound_id=? ORDER BY id", (result["id"],)).fetchone())
        self.assertEqual((before.get("length"), before.get("width"), before.get("thickness")), (1290, 1250, 1.4))
        self.assertEqual((before["item_name"], before["supplier_name"], before["location_code"], before["remark"]),
                         ("实际镀锌板", "供应商甲", "RAW-A", "机架"))
        self.conn.execute("UPDATE suppliers SET name='新供应商' WHERE id=1")
        self.conn.execute("UPDATE warehouse_locations SET name='新库位',code='NEW' WHERE id=1")
        self.conn.execute("UPDATE purchase_inventory_lots SET item_name='变更',length=1 WHERE id=?", (self.raw_a,))
        self.assertEqual(dict(self.conn.execute("SELECT * FROM purchase_inventory_outbound_items WHERE id=?", (before["id"],)).fetchone()), before)

    def test_dimension_schema_upgrade_is_idempotent(self):
        present = {r[1] for r in self.conn.execute("PRAGMA table_info(purchase_inventory_outbound_items)")}
        for field in ("length", "width", "height", "thickness"):
            if field in present:
                self.conn.execute(f"ALTER TABLE purchase_inventory_outbound_items DROP COLUMN {field}")
        pi.ensure_purchase_inventory_tables(self.conn)
        pi.ensure_purchase_inventory_tables(self.conn)
        self.assertTrue({"length", "width", "height", "thickness"} <= {r[1] for r in self.conn.execute("PRAGMA table_info(purchase_inventory_outbound_items)")})

    def test_void_restores_original_lots_once_with_audit_even_when_disabled(self):
        posted = self.outbound()
        self.conn.execute("UPDATE warehouse_locations SET enabled=0 WHERE id=1")
        result = self.void(posted["id"])
        self.assertEqual((result["status"], result["voided_by"], result["voided_at"]), ("voided", "supervisor", "2026-09-13T12:00:00"))
        self.assertEqual((self.lot(self.raw_a)["available_quantity"], self.lot(self.raw_b)["available_quantity"]), (10, 7))
        self.assertEqual((self.lot(self.raw_a)["version"], self.lot(self.raw_b)["version"]), (3, 3))
        tx = self.conn.execute("SELECT * FROM purchase_inventory_transactions WHERE transaction_type='reversal' ORDER BY lot_id").fetchall()
        self.assertEqual([r["quantity_delta"] for r in tx], [2, 3])
        self.assertTrue(all(r["operator"] == "supervisor" and r["to_location_id"] == 1 and r["related_id"] == posted["id"] for r in tx))
        before = self.state()
        self.assertEqual(self.void(posted["id"], actor="other"), result)
        self.assertEqual(self.outbound()["status"], "voided")
        self.assertEqual(self.state(), before)
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])
        self.assertFalse(pi.receipt_voidability(self.conn, self.lot(self.raw_a)["receipt_id"])[0])

    def test_mid_write_failure_rolls_back_post_and_void(self):
        self.conn.execute("CREATE TRIGGER fail_out BEFORE INSERT ON purchase_inventory_transactions WHEN NEW.transaction_type='outbound' AND NEW.lot_id=" + str(self.raw_b) + " BEGIN SELECT RAISE(ABORT,'test failure'); END")
        before = self.state()
        with self.assertRaises(sqlite3.IntegrityError):
            self.outbound()
        self.assertEqual(self.state(), before)
        self.conn.execute("DROP TRIGGER fail_out")
        posted = self.outbound()
        self.conn.execute("CREATE TRIGGER fail_void BEFORE INSERT ON purchase_inventory_transactions WHEN NEW.transaction_type='reversal' AND NEW.lot_id=" + str(self.raw_b) + " BEGIN SELECT RAISE(ABORT,'test failure'); END")
        before = self.state()
        with self.assertRaises(sqlite3.IntegrityError):
            self.void(posted["id"])
        self.assertEqual(self.state(), before)

    def test_writer_is_reserved_before_balance_reads_in_owned_and_deferred_transactions(self):
        for deferred in (False, True):
            with self.subTest(deferred=deferred), tempfile.TemporaryDirectory() as folder:
                first = sqlite3.connect(folder + "/stock.sqlite", timeout=0)
                first.row_factory = sqlite3.Row
                self.conn.backup(first)
                second = sqlite3.connect(folder + "/stock.sqlite", timeout=0)
                if deferred:
                    first.execute("BEGIN")
                attempted = []
                def probe(sql):
                    if not attempted and sql.lstrip().upper().startswith("SELECT") and "purchase_inventory_lots" in sql:
                        try:
                            second.execute("BEGIN IMMEDIATE")
                        except sqlite3.OperationalError:
                            attempted.append("locked")
                        else:
                            attempted.append("unlocked")
                            second.rollback()
                first.set_trace_callback(probe)
                try:
                    self.outbound(conn=first)
                    self.assertEqual(attempted, ["locked"])
                finally:
                    first.close()
                    second.close()

    def test_all_categories_support_only_explicitly_selected_lots(self):
        for category in ("raw_material", "carton", "outsourcing", "other"):
            oid, iid = self.create_order(category)
            payload = self.payload(oid=oid, item_id=iid, key=category, actual="4", qualified="4")
            payload["rows"][0]["height"] = "3"
            receipt = self.post(payload)
            lot_id = self.scalar("SELECT id FROM purchase_inventory_lots WHERE receipt_id=?", receipt["id"])
            with self.subTest(category=category):
                result = self.outbound(self.data(category=category, idempotency_key="out-" + category,
                    rows=[dict(lot_id=lot_id, quantity=4, expected_version=1, remark="整批")]))
                self.assertEqual(self.lot(lot_id)["available_quantity"], 0)
                self.void(result["id"])
                self.assertEqual(self.lot(lot_id)["available_quantity"], 4)
        self.assertEqual((self.lot(self.raw_a)["available_quantity"], self.lot(self.raw_b)["available_quantity"]), (10, 7))
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_mixed_categories_in_one_command_are_rejected(self):
        oid, iid = self.create_order("carton")
        payload = self.payload(oid=oid, item_id=iid, key="carton", actual="4", qualified="4")
        payload["rows"][0]["height"] = "3"
        receipt = self.post(payload)
        lot_id = self.scalar("SELECT id FROM purchase_inventory_lots WHERE receipt_id=?", receipt["id"])
        data = self.data()
        data["rows"][1]["lot_id"] = lot_id
        before = self.state()
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.outbound(data)
        self.assertEqual(self.state(), before)

    def test_transferred_lot_void_restores_that_lot_not_its_origin(self):
        moved = pi.transfer_purchase_inventory(self.conn, self.raw_a, 6, 2, "移库", "keeper", self.now, 1, "move")
        target = moved["target_lot_id"]
        posted = self.outbound(self.data(rows=[dict(lot_id=target, quantity=2, expected_version=1, remark="领用")]))
        pi.adjust_purchase_inventory(self.conn, target, 3, "清点", "keeper", self.now, 2, "adjust")
        self.void(posted["id"])
        self.assertEqual((self.lot(self.raw_a)["available_quantity"], self.lot(target)["available_quantity"]), (4, 5))
        self.assertEqual(self.lot(target)["version"], 4)
        self.assertEqual(pi.purchase_inventory_invariant_errors(self.conn), [])

    def test_two_connections_reject_stale_second_submission_after_first_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            first = sqlite3.connect(folder + "/stock.sqlite", timeout=0)
            first.row_factory = sqlite3.Row
            self.conn.backup(first)
            second = sqlite3.connect(folder + "/stock.sqlite", timeout=0)
            second.row_factory = sqlite3.Row
            try:
                self.outbound(conn=first)
                first.commit()
                with self.assertRaises(pi.PurchaseInventoryConflict) as error:
                    self.outbound(self.data(idempotency_key="other-request"), conn=second)
                self.assertEqual(error.exception.current["available_quantity"], 8)
                self.assertEqual(second.execute("SELECT COUNT(*) FROM purchase_inventory_outbounds").fetchone()[0], 1)
                self.assertEqual(pi.purchase_inventory_invariant_errors(second), [])
            finally:
                first.close()
                second.close()

    def test_outbound_and_void_do_not_write_outside_independent_inventory(self):
        before = {table: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY id")]
                  for table in ("suppliers", "purchase_orders", "purchase_order_items", "purchase_receipts",
                                "purchase_receipt_items", "warehouse_locations")}
        changed_tables = set()
        def authorize(action, table, column, database, source):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                changed_tables.add(table)
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:
            result = self.outbound()
            self.conn.commit()
            self.void(result["id"])
        finally:
            self.conn.set_authorizer(None)
        self.assertTrue(changed_tables <= {"purchase_inventory_lots", "purchase_inventory_outbounds",
                                          "purchase_inventory_outbound_items", "purchase_inventory_transactions"})
        for table, rows in before.items():
            self.assertEqual([tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY id")], rows)

    def test_missing_outbound_cannot_be_voided(self):
        with self.assertRaises(pi.PurchaseInventoryConflict):
            self.void(999)


if __name__ == "__main__":
    unittest.main()
