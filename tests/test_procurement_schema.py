import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
import procurement


class ProcurementSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.conn.close()

    def add_supplier(self, code="SUP-001", name="供应商甲", conn=None):
        conn = conn or self.conn
        conn.execute(
            """
            INSERT INTO suppliers (code, name, contact, phone, email, address, remark, created_at, updated_at)
            VALUES (?, ?, '', '', '', '', '', '2026-09-09T10:00:00', '2026-09-09T10:00:00')
            """,
            (code, name),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def add_order(self, order_no, supplier_id, conn=None):
        conn = conn or self.conn
        conn.execute(
            """
            INSERT INTO purchase_orders (
                order_no, category, supplier_id, supplier_code, supplier_name,
                supplier_contact, supplier_phone, supplier_email, supplier_address,
                purchased_at, delivery_address, recipient, recipient_phone, remark,
                created_by, updated_by, created_at, updated_at
            ) VALUES (?, 'raw_material', ?, 'SUP-001', '供应商甲', '', '', '', '',
                      '2026-09-09', '', '', '', '', 'admin', 'admin',
                      '2026-09-09T10:00:00', '2026-09-09T10:00:00')
            """,
            (order_no, supplier_id),
        )

    def assert_fractional_item_value_is_rejected(
        self, *, ordered_quantity=1, unit_price_minor=None, line_total_minor=None
    ):
        procurement.ensure_procurement_tables(self.conn)
        supplier_id = self.add_supplier()
        self.add_order("PO-20260909-0001", supplier_id)
        order_id = self.conn.execute("SELECT id FROM purchase_orders").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO purchase_order_items (
                    purchase_order_id, sort_order, item_name, drawing_no, material,
                    dimension_text, surface, spec, unit, expected_at, remark,
                    ordered_quantity, unit_price_minor, line_total_minor, created_at, updated_at
                ) VALUES (?, 1, '板材', '', '', '', '', '', '件', '', '', ?, ?, ?, 'now', 'now')
                """,
                (order_id, ordered_quantity, unit_price_minor, line_total_minor),
            )

    def test_schema_is_idempotent_and_order_number_fills_gaps(self):
        """A re-run must retain the schema and choose the first free daily suffix."""
        procurement.ensure_procurement_tables(self.conn)
        procurement.ensure_procurement_tables(self.conn)
        supplier_id = self.add_supplier()
        self.add_order("PO-20260909-0001", supplier_id)
        self.add_order("PO-20260909-0003", supplier_id)

        self.assertEqual(
            procurement.next_purchase_order_no(self.conn, "2026-09-09"),
            "PO-20260909-0002",
        )
        self.assertEqual(
            procurement.next_purchase_order_no(self.conn, "2026-09-10"),
            "PO-20260910-0001",
        )

    def test_schema_enforces_procurement_relationships_and_business_constraints(self):
        """Broken supplier/order links, duplicate legacy rows, and invalid values are rejected."""
        procurement.ensure_procurement_tables(self.conn)
        supplier_id = self.add_supplier()
        self.add_order("PO-20260909-0001", supplier_id)
        order_id = self.conn.execute("SELECT id FROM purchase_orders").fetchone()[0]

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO suppliers (code, name, contact, phone, email, address, remark, created_at, updated_at) "
                "VALUES ('', '空编码', '', '', '', '', '', 'now', 'now')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO supplier_legacy_links (supplier_id, legacy_source, legacy_id) VALUES (?, 'carton', 7)",
                (supplier_id,),
            )
            self.conn.execute(
                "INSERT INTO supplier_legacy_links (supplier_id, legacy_source, legacy_id) VALUES (?, 'carton', 7)",
                (supplier_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE purchase_orders SET category = 'invalid' WHERE id = ?", (order_id,)
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO purchase_order_items (purchase_order_id, sort_order, item_name, drawing_no, material, dimension_text, surface, spec, unit, expected_at, remark, ordered_quantity, unit_price_minor, created_at, updated_at) "
                "VALUES (?, 1, '板材', '', '', '', '', '', '件', '', '', 0, -1, 'now', 'now')",
                (order_id,),
            )

        self.conn.execute(
            "INSERT INTO purchase_order_items (purchase_order_id, sort_order, item_name, drawing_no, material, dimension_text, surface, spec, unit, expected_at, remark, ordered_quantity, created_at, updated_at) "
            "VALUES (?, 1, '板材', '', '', '', '', '', '件', '', '', 1, 'now', 'now')",
            (order_id,),
        )
        self.conn.execute("DELETE FROM purchase_orders WHERE id = ?", (order_id,))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM purchase_order_items").fetchone()[0], 0
        )

    def test_delivery_profile_allows_only_one_active_default(self):
        """Two active default destinations would make automatic delivery selection ambiguous."""
        procurement.ensure_procurement_tables(self.conn)
        columns = "name, delivery_address, recipient, phone, default_remark, is_default, active, created_at, updated_at"
        self.conn.execute(
            f"INSERT INTO purchase_delivery_profiles ({columns}) VALUES ('地址一', '地址', '收件人', '123', '', 1, 1, 'now', 'now')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                f"INSERT INTO purchase_delivery_profiles ({columns}) VALUES ('地址二', '地址', '收件人', '123', '', 1, 1, 'now', 'now')"
            )
        self.conn.execute(
            f"INSERT INTO purchase_delivery_profiles ({columns}) VALUES ('地址三', '地址', '收件人', '123', '', 1, 0, 'now', 'now')"
        )

    def test_purchase_values_are_strict(self):
        """Malformed quantities, money, categories, and dates must not enter procurement writes."""
        self.assertEqual(procurement.parse_purchase_quantity("12"), 12)
        self.assertEqual(procurement.parse_optional_money_minor("18.50"), 1850)
        self.assertIsNone(procurement.parse_optional_money_minor(""))
        self.assertEqual(procurement.parse_purchase_category("carton"), "carton")
        for value in ("0", "-1", "1.2", "1e2", True, "2147483648"):
            with self.subTest(quantity=value), self.assertRaises(ValueError):
                procurement.parse_purchase_quantity(value)
        for value in ("", "raw material", "RAW_MATERIAL", None):
            with self.subTest(category=value), self.assertRaises(ValueError):
                procurement.parse_purchase_category(value)
        procurement.ensure_procurement_tables(self.conn)
        for value in ("2026-02-29", "09-09-2026", "2026-09-09T00:00:00"):
            with self.subTest(purchased_at=value), self.assertRaises(ValueError):
                procurement.next_purchase_order_no(self.conn, value)

    def test_purchase_item_rejects_fractional_ordered_quantity(self):
        """A fractional quantity would make receiving and inventory totals ambiguous."""
        self.assert_fractional_item_value_is_rejected(ordered_quantity=1.5)

    def test_purchase_item_rejects_fractional_unit_price_minor(self):
        """A fractional minor-unit price would corrupt monetary arithmetic."""
        self.assert_fractional_item_value_is_rejected(unit_price_minor=1.5)

    def test_purchase_item_rejects_fractional_line_total_minor(self):
        """A fractional minor-unit total would corrupt order totals."""
        self.assert_fractional_item_value_is_rejected(line_total_minor=1.5)

    def test_init_db_creates_schema_idempotently_without_foreign_key_violations(self):
        """Application startup must create the new schema repeatedly alongside legacy tables."""
        original_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = Path(tmpdir) / "manuals.db"
                app.init_db()
                app.init_db()
                with app.get_db() as conn:
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    self.assertTrue(
                        {"suppliers", "supplier_legacy_links", "purchase_delivery_profiles", "purchase_orders", "purchase_order_items"}.issubset(tables)
                    )
                    self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            app.DB_PATH = original_db_path

    def test_production_connection_enforces_procurement_foreign_keys_and_cascades(self):
        """Procurement writes through app.get_db must reject orphans and cascade item deletion."""
        original_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = Path(tmpdir) / "manuals.db"
                app.init_db()
                with app.get_db() as conn:
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.add_order("PO-20260909-0001", 999, conn)
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO supplier_legacy_links (supplier_id, legacy_source, legacy_id) "
                            "VALUES (999, 'carton', 1)"
                        )

                    supplier_id = self.add_supplier(conn=conn)
                    conn.execute(
                        "INSERT INTO supplier_legacy_links (supplier_id, legacy_source, legacy_id) "
                        "VALUES (?, 'carton', 1)",
                        (supplier_id,),
                    )
                    self.add_order("PO-20260909-0002", supplier_id, conn)
                    order_id = conn.execute("SELECT id FROM purchase_orders").fetchone()[0]
                    conn.execute(
                        """
                        INSERT INTO purchase_order_items (
                            purchase_order_id, sort_order, item_name, drawing_no, material,
                            dimension_text, surface, spec, unit, expected_at, remark,
                            ordered_quantity, created_at, updated_at
                        ) VALUES (?, 1, '板材', '', '', '', '', '', '件', '', '', 1, 'now', 'now')
                        """,
                        (order_id,),
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
                    conn.execute("DELETE FROM purchase_orders WHERE id = ?", (order_id,))
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM purchase_order_items").fetchone()[0],
                        0,
                    )
                    conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM supplier_legacy_links").fetchone()[0],
                        0,
                    )
        finally:
            app.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
