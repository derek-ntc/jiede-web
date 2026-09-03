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


class ProductPricePermissionTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.original_testing = app.app.config["TESTING"]
        self.original_secret_key = app.app.config["SECRET_KEY"]
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        app.init_db()
        self.create_user("plain")
        self.create_user("price-reader", can_view_prices=1)
        self.create_user("finance", can_manage_finance=1)

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(TESTING=self.original_testing, SECRET_KEY=self.original_secret_key)
        self.tmpdir.cleanup()

    def create_user(self, username, can_view_prices=0, can_manage_finance=0):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, created_at, updated_at,
                    can_manage_products, can_manage_orders, can_view_orders,
                    can_manage_shipped, can_view_shipped, can_manage_customers,
                    can_manage_common_info, can_manage_purchase_followups,
                    can_manage_powder_coating, can_manage_carton_purchases,
                    can_manage_warehouse_inventory, can_manage_production_followups,
                    can_create_products, can_edit_products,
                    can_view_prices, can_manage_finance
                ) VALUES (?, 'hash', 'operator', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
                """,
                (username, now, now, can_view_prices, can_manage_finance),
            )

    def login_as(self, username):
        with app.app.test_request_context("/"):
            from flask import session

            session["admin_logged_in"] = True
            session["admin_username"] = username
            return (
                app.user_has_permission("price_view"),
                app.user_has_permission("finance_manage"),
                app.user_can_view_prices(),
            )

    def test_price_and_finance_permissions_are_granted_only_by_their_flags(self):
        self.assertEqual(self.login_as("plain"), (False, False, False))
        self.assertEqual(self.login_as("price-reader"), (True, False, True))
        self.assertEqual(self.login_as("finance"), (False, True, True))

    def test_admin_user_forms_persist_price_and_finance_flags(self):
        client = app.app.test_client()
        with client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"

        response = client.post(
            "/admin/users",
            data={
                "username": "form-user",
                "password": "password",
                "role": "operator",
                "can_view_prices": "on",
                "can_manage_finance": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            user = conn.execute(
                "SELECT id, can_view_prices, can_manage_finance FROM users WHERE username = 'form-user'"
            ).fetchone()
        self.assertEqual((user["can_view_prices"], user["can_manage_finance"]), (1, 1))

        response = client.post(
            f"/admin/users/{user['id']}/edit",
            data={"role": "operator", "can_view_prices": "on"},
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            user = conn.execute(
                "SELECT can_view_prices, can_manage_finance FROM users WHERE username = 'form-user'"
            ).fetchone()
        self.assertEqual((user["can_view_prices"], user["can_manage_finance"]), (1, 0))
