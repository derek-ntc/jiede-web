import tempfile
import unittest
from pathlib import Path

import app


class ProductPricingMigrationTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def test_pricing_columns_defaults_and_legacy_rows_survive_reinitialization(self):
        app.init_db()
        expected = {
            "manuals": {"unit_price_minor", "currency"},
            "product_order_shipments": {
                "unit_price_minor",
                "currency",
                "price_recorded_by",
                "price_recorded_at",
            },
            "assembly_shipment_items": {
                "unit_price_minor",
                "currency",
                "price_recorded_by",
                "price_recorded_at",
            },
        }
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            for table, columns in expected.items():
                actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertTrue(columns <= actual)

            user_id = conn.execute(
                """
                INSERT INTO users (username, password_hash, created_at, updated_at)
                VALUES ('ordinary-user', 'hash', ?, ?)
                """,
                (now, now),
            ).lastrowid
            manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    product_name, model, category, version, filename, original_filename,
                    created_at, updated_at
                ) VALUES ('Legacy product', '', '', '', '', '', ?, ?)
                """,
                (now, now),
            ).lastrowid
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, planned_ship_at, created_at, updated_at
                ) VALUES (?, 'SO-LEGACY', '2026-09-03', 1, '2026-09-04', ?, ?)
                """,
                (manual_id, now, now),
            ).lastrowid
            shipment_id = conn.execute(
                """
                INSERT INTO product_order_shipments (order_id, shipped_quantity, shipped_at, created_at)
                VALUES (?, 1, '2026-09-03', ?)
                """,
                (order_id, now),
            ).lastrowid
            user = conn.execute(
                "SELECT can_view_prices, can_manage_finance FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            shipment = conn.execute(
                "SELECT unit_price_minor FROM product_order_shipments WHERE id = ?", (shipment_id,)
            ).fetchone()
            self.assertEqual((user["can_view_prices"], user["can_manage_finance"]), (0, 0))
            self.assertIsNone(shipment["unit_price_minor"])

        app.init_db()

        with app.get_db() as conn:
            user = conn.execute(
                "SELECT can_view_prices, can_manage_finance FROM users WHERE username = 'ordinary-user'"
            ).fetchone()
            shipment = conn.execute(
                "SELECT unit_price_minor FROM product_order_shipments WHERE id = ?", (shipment_id,)
            ).fetchone()
            self.assertEqual((user["can_view_prices"], user["can_manage_finance"]), (0, 0))
            self.assertIsNone(shipment["unit_price_minor"])
