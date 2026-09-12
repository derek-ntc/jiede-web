import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
import procurement
from procurement_inventory import (
    ensure_purchase_inventory_tables,
    parse_purchase_category_slug,
    parse_purchase_inventory_quantity,
    purchase_inventory_invariant_errors,
)


class PurchaseInventorySchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        procurement.ensure_procurement_tables(self.conn)
        self.conn.execute(
            """
            CREATE TABLE warehouse_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                remark TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_purchase_inventory_tables(self.conn)

    def tearDown(self):
        self.conn.close()

    def seed_order_context(self):
        self.conn.execute(
            """
            INSERT INTO suppliers (
                code, name, contact, phone, email, address, remark,
                created_at, updated_at
            ) VALUES ('SUP-001', '供应商甲', '', '', '', '', '', 'now', 'now')
            """
        )
        supplier_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_orders (
                order_no, category, supplier_id, supplier_code, supplier_name,
                supplier_contact, supplier_phone, supplier_email, supplier_address,
                purchased_at, delivery_address, recipient, recipient_phone, remark,
                created_by, updated_by, created_at, updated_at
            ) VALUES (
                'PO-20260912-0001', 'raw_material', ?, 'SUP-001', '供应商甲',
                '', '', '', '', '2026-09-12', '', '', '', '',
                'buyer', 'buyer', 'now', 'now'
            )
            """,
            (supplier_id,),
        )
        order_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_order_items (
                purchase_order_id, sort_order, item_name, drawing_no, material,
                dimension_text, surface, spec, unit, expected_at, remark,
                ordered_quantity, created_at, updated_at
            ) VALUES (?, 1, '钢板', 'DWG-1', 'Q235', '100x50', '喷砂', '', '张',
                      '2026-09-20', '', 10, 'now', 'now')
            """,
            (order_id,),
        )
        order_item_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO warehouse_locations
                (name, code, remark, enabled, created_at, updated_at)
            VALUES ('原料区', 'RAW-A', '', 1, 'now', 'now')
            """
        )
        location_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return supplier_id, order_id, order_item_id, location_id

    def seed_receipt_lot(self, *, qualified_quantity=4, opening_quantity=4):
        supplier_id, order_id, order_item_id, location_id = self.seed_order_context()
        self.conn.execute(
            """
            INSERT INTO purchase_receipts (
                receipt_no, purchase_order_id, received_at, status, remark,
                idempotency_key, payload_hash, created_by, posted_by,
                created_at, posted_at
            ) VALUES ('PR-1', ?, '2026-09-12T10:00:00', 'posted', '',
                      'receipt-key', 'receipt-hash', 'buyer', 'buyer', 'now', 'now')
            """,
            (order_id,),
        )
        receipt_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_receipt_items (
                receipt_id, purchase_order_item_id, item_name, drawing_no,
                material, dimension_text, surface, spec, unit,
                actual_quantity, qualified_quantity, location_id,
                invoice_status, remark
            ) VALUES (?, ?, '钢板', 'DWG-1', 'Q235', '100x50', '喷砂', '', '张',
                      5, ?, ?, 'pending', '')
            """,
            (receipt_id, order_item_id, qualified_quantity, location_id),
        )
        receipt_item_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_lots (
                lot_no, category, origin_receipt_item_id, source_lot_id, source_kind,
                item_name, drawing_no, material, dimension_text, surface, spec, unit,
                supplier_id, supplier_name, purchase_order_id, purchase_order_item_id,
                receipt_id, location_id, opening_quantity, available_quantity,
                unit_price_minor, currency, invoice_status, created_at, updated_at
            ) VALUES ('LOT-1', 'raw_material', ?, NULL, 'receipt',
                      '钢板', 'DWG-1', 'Q235', '100x50', '喷砂', '', '张',
                      ?, '供应商甲', ?, ?, ?, ?, ?, ?, NULL, 'CNY', 'pending', 'now', 'now')
            """,
            (
                receipt_item_id,
                supplier_id,
                order_id,
                order_item_id,
                receipt_id,
                location_id,
                opening_quantity,
                opening_quantity,
            ),
        )
        lot_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return supplier_id, order_id, order_item_id, location_id, receipt_id, receipt_item_id, lot_id

    def test_schema_is_idempotent_and_uses_shared_locations(self):
        """Schema reruns must preserve all tables and keep locations shared."""
        ensure_purchase_inventory_tables(self.conn)
        ensure_purchase_inventory_tables(self.conn)
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue(
            {
                "purchase_receipts",
                "purchase_receipt_items",
                "purchase_inventory_lots",
                "purchase_inventory_operations",
                "purchase_inventory_transactions",
                "purchase_inventory_invoice_events",
                "purchase_inventory_outbounds",
                "purchase_inventory_outbound_items",
            }
            <= names
        )
        foreign = self.conn.execute(
            "PRAGMA foreign_key_list(purchase_inventory_lots)"
        ).fetchall()
        self.assertTrue(
            any(
                row[2] == "warehouse_locations" and row[3] == "location_id"
                for row in foreign
            )
        )

    def test_invalid_category_quantity_and_status_are_rejected(self):
        """Invalid domain values must not cross the inventory boundary."""
        with self.assertRaises(ValueError):
            parse_purchase_category_slug("raw")
        with self.assertRaises(ValueError):
            parse_purchase_inventory_quantity("0")
        schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='purchase_receipts'"
        ).fetchone()[0]
        self.assertIn(
            "CHECK status IN ('posted','voided')", " ".join(schema.split())
        )

    def test_value_parsers_reject_lossy_or_out_of_range_inputs(self):
        """Parser boundaries must reject normalization, booleans, fractions, and overflow."""
        for category in procurement.PURCHASE_CATEGORIES:
            with self.subTest(category=category):
                self.assertEqual(parse_purchase_category_slug(category), category)
        for value in ("", "RAW_MATERIAL", "raw material", None, True):
            with self.subTest(category=value), self.assertRaises(ValueError):
                parse_purchase_category_slug(value)

        self.assertEqual(parse_purchase_inventory_quantity("1"), 1)
        self.assertEqual(
            parse_purchase_inventory_quantity("0", allow_zero=True), 0
        )
        for value in (0, "0", -1, "1.0", "1e2", True, "2147483648"):
            with self.subTest(quantity=value), self.assertRaises(ValueError):
                parse_purchase_inventory_quantity(value)
        for value in (-1, "-1", "1.0", True, "2147483648"):
            with self.subTest(zero_quantity=value), self.assertRaises(ValueError):
                parse_purchase_inventory_quantity(value, allow_zero=True)

    def test_quantity_constraints_require_integer_storage_and_valid_bounds(self):
        """Fractional, negative, and internally inconsistent quantities must be rejected."""
        supplier_id, _, order_item_id, location_id = self.seed_order_context()
        order_id = self.conn.execute("SELECT id FROM purchase_orders").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_receipts (
                receipt_no, purchase_order_id, received_at, status, remark,
                idempotency_key, payload_hash, created_by, posted_by,
                created_at, posted_at
            ) VALUES ('PR-1', ?, 'now', 'posted', '', 'key', 'hash', 'buyer', 'buyer', 'now', 'now')
            """,
            (order_id,),
        )
        receipt_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for actual, qualified in ((1.5, 1), (1, 0.5), (1, 2), (1, -1)):
            with self.subTest(actual=actual, qualified=qualified), self.assertRaises(
                sqlite3.IntegrityError
            ):
                self.conn.execute(
                    """
                    INSERT INTO purchase_receipt_items (
                        receipt_id, purchase_order_item_id, item_name, drawing_no,
                        material, dimension_text, surface, spec, unit,
                        actual_quantity, qualified_quantity, location_id,
                        invoice_status, remark
                    ) VALUES (?, ?, '钢板', '', '', '', '', '', '张', ?, ?, ?, 'pending', '')
                    """,
                    (receipt_id, order_item_id, actual, qualified, location_id),
                )

        self.conn.execute(
            """
            INSERT INTO purchase_receipt_items (
                receipt_id, purchase_order_item_id, item_name, drawing_no,
                material, dimension_text, surface, spec, unit,
                actual_quantity, qualified_quantity, location_id,
                invoice_status, remark
            ) VALUES (?, ?, '钢板', '', '', '', '', '', '张', 2, 2, ?, 'pending', '')
            """,
            (receipt_id, order_item_id, location_id),
        )
        receipt_item_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_lots (
                lot_no, category, origin_receipt_item_id, source_lot_id, source_kind,
                item_name, drawing_no, material, dimension_text, surface, spec, unit,
                supplier_id, supplier_name, purchase_order_id, purchase_order_item_id,
                receipt_id, location_id, opening_quantity, available_quantity,
                currency, invoice_status, created_at, updated_at
            ) VALUES ('LOT-1', 'raw_material', ?, NULL, 'receipt', '钢板', '', '', '', '', '', '张',
                      ?, '供应商甲', ?, ?, ?, ?, 2, 2, 'CNY', 'pending', 'now', 'now')
            """,
            (receipt_item_id, supplier_id, order_id, order_item_id, receipt_id, location_id),
        )
        lot_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                UPDATE purchase_inventory_lots
                SET source_lot_id = id
                WHERE id = ?
                """,
                (lot_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE purchase_inventory_lots SET available_quantity = -1 WHERE id = ?",
                (lot_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO purchase_inventory_transactions (
                    transaction_no, lot_id, transaction_type, quantity_delta,
                    operator, created_at
                ) VALUES ('TX-FRACTION', ?, 'adjustment', 0.5, 'buyer', 'now')
                """,
                (lot_id,),
            )

    def test_query_indexes_cover_inventory_filters(self):
        """Operational filters must have stable indexes rather than full table scans."""
        expected = {
            "idx_purchase_receipts_order",
            "idx_purchase_receipts_status",
            "idx_purchase_receipts_received_at",
            "idx_purchase_receipt_items_order_item",
            "idx_purchase_receipt_items_location",
            "idx_purchase_inventory_lots_category",
            "idx_purchase_inventory_lots_supplier",
            "idx_purchase_inventory_lots_order",
            "idx_purchase_inventory_lots_receipt",
            "idx_purchase_inventory_lots_location",
            "idx_purchase_inventory_lots_item_name",
            "idx_purchase_inventory_lots_drawing_no",
            "idx_purchase_inventory_lots_invoice_status",
            "idx_purchase_inventory_lots_created_at",
            "idx_purchase_inventory_transactions_lot_created_at",
            "idx_purchase_inventory_transactions_type_created_at",
            "idx_purchase_inventory_outbounds_status",
            "idx_purchase_inventory_outbounds_outbound_at",
            "idx_purchase_inventory_outbound_items_outbound",
            "idx_purchase_inventory_outbound_items_lot",
        }
        actual = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertTrue(expected <= actual, expected - actual)

    def test_relationship_triggers_protect_production_connections(self):
        """Relationships must hold even when production SQLite foreign keys are disabled."""
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO purchase_receipts (
                    receipt_no, purchase_order_id, received_at, status, remark,
                    idempotency_key, payload_hash, created_by, posted_by,
                    created_at, posted_at
                ) VALUES ('ORPHAN', 999, 'now', 'posted', '', 'orphan', 'hash',
                          'buyer', 'buyer', 'now', 'now')
                """
            )

        *_, location_id, _, _, _ = self.seed_receipt_lot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "DELETE FROM warehouse_locations WHERE id = ?", (location_id,)
            )

    def test_invariant_checker_reports_each_required_corruption_class(self):
        """The audit helper must expose every corruption class needed for safe operations."""
        (
            supplier_id,
            order_id,
            order_item_id,
            location_id,
            receipt_id,
            receipt_item_id,
            lot_id,
        ) = self.seed_receipt_lot(qualified_quantity=4, opening_quantity=3)
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_lots (
                lot_no, category, origin_receipt_item_id, source_lot_id, source_kind,
                item_name, drawing_no, material, dimension_text, surface, spec, unit,
                supplier_id, supplier_name, purchase_order_id, purchase_order_item_id,
                receipt_id, location_id, opening_quantity, available_quantity,
                currency, invoice_status, created_at, updated_at
            ) VALUES ('LOT-2', 'raw_material', ?, ?, 'transfer', '钢板', 'DWG-1',
                      'Q235', '100x50', '喷砂', '', '张', ?, '供应商甲', ?, ?, ?, ?,
                      2, 2, 'CNY', 'pending', 'now', 'now')
            """,
            (receipt_item_id, lot_id, supplier_id, order_id, order_item_id, receipt_id, location_id),
        )
        transfer_lot_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_transactions (
                transaction_no, lot_id, transaction_type, quantity_delta,
                from_location_id, operator, created_at
            ) VALUES ('TX-OUT', ?, 'transfer_out', -2, ?, 'buyer', 'now')
            """,
            (lot_id, location_id),
        )
        out_transaction_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_transactions (
                transaction_no, lot_id, transaction_type, quantity_delta,
                to_location_id, paired_transaction_id, operator, created_at
            ) VALUES ('TX-IN', ?, 'transfer_in', 1, ?, ?, 'buyer', 'now')
            """,
            (transfer_lot_id, location_id, out_transaction_id),
        )
        in_transaction_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            "UPDATE purchase_inventory_transactions SET paired_transaction_id=? WHERE id=?",
            (in_transaction_id, out_transaction_id),
        )
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_outbounds (
                outbound_no, outbound_at, used_by, operator, remark, status,
                idempotency_key, payload_hash, created_at
            ) VALUES ('OB-1', 'now', '车间', 'buyer', '', 'posted', 'out-key', 'hash', 'now')
            """
        )
        outbound_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO purchase_inventory_outbound_items (
                outbound_id, lot_id, category, item_name, drawing_no, material,
                dimension_text, surface, spec, unit, supplier_name, location_id,
                location_code, location_name, quantity, remark
            ) VALUES (?, ?, 'raw_material', '钢板', 'DWG-1', 'Q235', '100x50',
                      '喷砂', '', '张', '供应商甲', ?, 'RAW-A', '原料区', 1, '')
            """,
            (outbound_id, lot_id, location_id),
        )

        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("PRAGMA ignore_check_constraints = ON")
        self.conn.execute(
            "UPDATE purchase_inventory_lots SET available_quantity=-1 WHERE id=?",
            (lot_id,),
        )
        self.conn.execute(
            "DROP TRIGGER IF EXISTS "
            "trg_purchase_inventory_lots_source_lot_id_require_update"
        )
        self.conn.execute(
            "UPDATE purchase_inventory_lots SET source_lot_id=999 WHERE id=?",
            (transfer_lot_id,),
        )

        errors = purchase_inventory_invariant_errors(self.conn)
        for expected in (
            "negative lot",
            "receipt opening quantity mismatch",
            "transfer pair quantity mismatch",
            "outbound item missing transaction",
            "orphaned source record",
        ):
            with self.subTest(expected=expected):
                self.assertTrue(
                    any(expected in error for error in errors),
                    f"{expected!r} absent from {errors!r}",
                )

    def test_init_db_wires_schema_idempotently_without_granting_permissions(self):
        """Startup must install the schema without changing an existing user's grants."""
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = Path(tmpdir) / "manuals.db"
                app.DATABASE_READY = False
                app.init_db()
                with app.get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO users (
                            username, password_hash, role, created_at, updated_at
                        ) VALUES ('schema-user', 'hash', 'operator', 'now', 'now')
                        """
                    )
                app.init_db()
                with app.get_db() as conn:
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    self.assertTrue(
                        {
                            "purchase_receipts",
                            "purchase_receipt_items",
                            "purchase_inventory_lots",
                            "purchase_inventory_operations",
                            "purchase_inventory_transactions",
                            "purchase_inventory_invoice_events",
                            "purchase_inventory_outbounds",
                            "purchase_inventory_outbound_items",
                        }
                        <= tables
                    )
                    grants = conn.execute(
                        """
                        SELECT can_receive_purchases,
                               can_view_purchase_inventory,
                               can_adjust_purchase_inventory,
                               can_outbound_purchase_inventory
                        FROM users
                        WHERE username = 'schema-user'
                        """
                    ).fetchone()
                    self.assertEqual(tuple(grants), (0, 0, 0, 0))
                    self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            app.DB_PATH = original_db_path
            app.DATABASE_READY = original_database_ready


if __name__ == "__main__":
    unittest.main()
