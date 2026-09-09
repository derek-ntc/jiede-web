import tempfile
import unittest
from pathlib import Path

import app
from flask import session


PROCUREMENT_PERMISSION_COLUMNS = (
    "can_view_purchases",
    "can_manage_purchases",
    "can_receive_purchases",
    "can_view_purchase_inventory",
    "can_adjust_purchase_inventory",
    "can_outbound_purchase_inventory",
    "can_view_purchase_prices",
    "can_manage_suppliers",
)


class ProcurementPermissionTests(unittest.TestCase):
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
        with app.get_db() as conn:
            existing = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)")
            }
            for column in PROCUREMENT_PERMISSION_COLUMNS:
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
        self.create_user("buyer", can_view_purchases=1)

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        self.tmpdir.cleanup()

    def create_user(self, username, **permissions):
        columns = ["username", "password_hash", "role", "created_at", "updated_at"]
        values = [username, "hash", "operator", "2026-09-09T10:00:00", "2026-09-09T10:00:00"]
        for column in PROCUREMENT_PERMISSION_COLUMNS:
            columns.append(column)
            values.append(permissions.get(column, 0))
        placeholders = ", ".join("?" for _ in columns)
        with app.get_db() as conn:
            conn.execute(
                f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )

    def login(self, username):
        self.client = app.app.test_client()
        with self.client.session_transaction() as client_session:
            client_session["admin_logged_in"] = True
            client_session["admin_username"] = username

    def restore_session(self, username):
        session["admin_logged_in"] = True
        session["admin_username"] = username

    def test_purchase_view_does_not_grant_purchase_price(self):
        self.login("buyer")
        with app.app.test_request_context("/admin/purchases/raw-material"):
            self.restore_session("buyer")
            self.assertTrue(app.user_has_permission("purchase_view"))
            self.assertFalse(app.user_can_view_purchase_prices())

    def test_supplier_mutation_requires_supplier_manage(self):
        self.login("buyer")

        @app.permission_required("supplier_manage")
        def supplier_mutation():
            return "mutated"

        with app.app.test_request_context("/admin/suppliers"):
            self.restore_session("buyer")
            response = supplier_mutation()
        self.assertEqual(response.status_code, 302)

    def test_admin_user_forms_persist_only_submitted_procurement_permissions(self):
        client = app.app.test_client()
        with client.session_transaction() as client_session:
            client_session["admin_logged_in"] = True
            client_session["admin_username"] = "admin"

        response = client.post(
            "/admin/users",
            data={
                "username": "form-buyer",
                "password": "password",
                "role": "operator",
                "can_view_purchases": "on",
                "can_manage_suppliers": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            user = conn.execute(
                """
                SELECT can_view_purchases, can_manage_purchases, can_receive_purchases,
                       can_view_purchase_inventory, can_adjust_purchase_inventory,
                       can_outbound_purchase_inventory, can_view_purchase_prices,
                       can_manage_suppliers
                FROM users WHERE username = 'form-buyer'
                """
            ).fetchone()
        self.assertEqual(tuple(user), (1, 0, 0, 0, 0, 0, 0, 1))


class ProcurementPermissionMigrationTests(unittest.TestCase):
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

    def test_new_procurement_columns_backfill_legacy_permissions_once(self):
        legacy_columns = (
            "can_manage_products", "can_manage_orders", "can_view_orders",
            "can_manage_shipped", "can_view_shipped", "can_manage_customers",
            "can_manage_common_info", "can_manage_purchase_followups",
            "can_manage_powder_coating", "can_manage_carton_purchases",
            "can_manage_warehouse_inventory", "can_manage_production_followups",
            "can_create_products", "can_edit_products", "can_view_prices",
            "can_manage_finance",
        )
        with app.get_db() as conn:
            conn.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    can_manage_products INTEGER NOT NULL DEFAULT 1,
                    can_manage_orders INTEGER NOT NULL DEFAULT 1,
                    can_view_orders INTEGER NOT NULL DEFAULT 1,
                    can_manage_shipped INTEGER NOT NULL DEFAULT 1,
                    can_view_shipped INTEGER NOT NULL DEFAULT 1,
                    can_manage_customers INTEGER NOT NULL DEFAULT 1,
                    can_manage_common_info INTEGER NOT NULL DEFAULT 1,
                    can_manage_purchase_followups INTEGER NOT NULL DEFAULT 1,
                    can_manage_powder_coating INTEGER NOT NULL DEFAULT 1,
                    can_manage_carton_purchases INTEGER NOT NULL DEFAULT 1,
                    can_manage_warehouse_inventory INTEGER NOT NULL DEFAULT 1,
                    can_manage_production_followups INTEGER NOT NULL DEFAULT 0,
                    can_create_products INTEGER NOT NULL DEFAULT 1,
                    can_edit_products INTEGER NOT NULL DEFAULT 1,
                    can_view_prices INTEGER NOT NULL DEFAULT 0,
                    can_manage_finance INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = ("username", "password_hash", "role", "created_at", "updated_at", *legacy_columns)
            values = (
                "legacy-buyer", "hash", "operator", "2026-09-09T10:00:00",
                "2026-09-09T10:00:00", 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0, 1,
            )
            conn.execute(
                f"INSERT INTO users ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
            app.ensure_user_table(conn)
            actual_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            self.assertTrue(set(PROCUREMENT_PERMISSION_COLUMNS) <= actual_columns)
            user = conn.execute(
                f"SELECT {', '.join(PROCUREMENT_PERMISSION_COLUMNS)} FROM users WHERE username = 'legacy-buyer'"
            ).fetchone()
        self.assertEqual(tuple(user), (1, 1, 1, 1, 1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
