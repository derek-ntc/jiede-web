import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
from flask import get_flashed_messages, session


class FinanceMigrationTests(unittest.TestCase):
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

    def test_finance_tables_customer_fields_and_live_source_claim_are_created_idempotently(self):
        app.init_db()
        expected = {
            "customers": {
                "invoice_title",
                "tax_id",
                "registered_address",
                "registered_phone",
                "bank_name",
                "bank_account",
                "invoice_email",
            },
            "users": {"can_view_prices", "can_manage_finance"},
        }
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            for table, columns in expected.items():
                actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertTrue(columns <= actual)
            self.assertIsNotNone(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'finance_invoices'"
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'finance_invoice_items'"
                ).fetchone()
            )
            customer_id = conn.execute(
                "INSERT INTO customers (name, created_at, updated_at) VALUES ('客户A', ?, ?)",
                (now, now),
            ).lastrowid
            invoice_id = conn.execute(
                """
                INSERT INTO finance_invoices (
                    customer_id, customer_name, currency, total_minor,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, '客户A', 'CNY', 100, 'finance', 'finance', ?, ?)
                """,
                (customer_id, now, now),
            ).lastrowid
            item = (
                invoice_id, "ordinary", 17, "ordinary:17", "SO-017", None, "", now,
                "D-017", "Product", 1, 100, "CNY", 100, now,
            )
            conn.execute(
                """
                INSERT INTO finance_invoice_items (
                    invoice_id, source_type, source_id, active_claim_key, order_no,
                    assembly_batch_id, assembly_drawing_no, shipped_at, drawing_no,
                    product_name, quantity, unit_price_minor, currency, line_total_minor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                item,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO finance_invoice_items (
                        invoice_id, source_type, source_id, active_claim_key, order_no,
                        assembly_drawing_no, shipped_at, drawing_no, product_name, quantity,
                        unit_price_minor, currency, line_total_minor, created_at
                    ) VALUES (?, 'ordinary', 17, 'ordinary:17', '', '', ?, '', '', 1, 100, 'CNY', 100, ?)
                    """,
                    (invoice_id, now, now),
                )

        app.init_db()


class FinancePermissionTests(unittest.TestCase):
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
                    can_create_products, can_edit_products, can_view_prices, can_manage_finance
                ) VALUES ('plain', 'hash', 'operator', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, created_at, updated_at,
                    can_manage_products, can_manage_orders, can_view_orders,
                    can_manage_shipped, can_view_shipped, can_manage_customers,
                    can_manage_common_info, can_manage_purchase_followups,
                    can_manage_powder_coating, can_manage_carton_purchases,
                    can_manage_warehouse_inventory, can_manage_production_followups,
                    can_create_products, can_edit_products, can_view_prices, can_manage_finance
                ) VALUES ('finance', 'hash', 'operator', ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
                """,
                (now, now),
            )

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(TESTING=self.original_testing, SECRET_KEY=self.original_secret_key)
        self.tmpdir.cleanup()

    def test_finance_permission_required_redirects_unauthorized_operator(self):
        @app.permission_required("finance_manage")
        def finance_view():
            return "finance"

        with app.app.test_request_context("/finance"):
            session["admin_logged_in"] = True
            session["admin_username"] = "plain"
            response = finance_view()
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/admin")
            self.assertIn(
                ("error", "当前账号没有权限访问该模块"),
                get_flashed_messages(with_categories=True),
            )

    def test_finance_permission_required_allows_finance_operator(self):
        @app.permission_required("finance_manage")
        def finance_view():
            return "finance"

        with app.app.test_request_context("/finance"):
            session["admin_logged_in"] = True
            session["admin_username"] = "finance"
            self.assertEqual(finance_view(), "finance")
