import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


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
