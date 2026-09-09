import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


def pdf_text(response):
    executable = shutil.which("pdftotext")
    if executable:
        result = subprocess.run(
            [executable, "-layout", "-", "-"],
            input=response.data,
            capture_output=True,
            check=True,
        )
        return "".join(result.stdout.decode().split())
    osascript = shutil.which("osascript")
    if osascript:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(response.data)
            pdf.flush()
            script = (
                "ObjC.import('Foundation'); ObjC.import('PDFKit'); "
                "function run(argv) { "
                "const url = $.NSURL.fileURLWithPath(argv[0]); "
                "const document = $.PDFDocument.alloc.initWithURL(url); "
                "if (!document) throw new Error('cannot open PDF'); "
                "return ObjC.unwrap(document.string); }"
            )
            result = subprocess.run(
                [osascript, "-l", "JavaScript", "-e", script, pdf.name],
                capture_output=True,
                check=True,
            )
        return "".join(result.stdout.decode().split())
    raise unittest.SkipTest("PDF text QA requires Poppler pdftotext or macOS PDFKit")


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

    def create_procurement_only_user(self, username, **permissions):
        self.create_user(username, **permissions)
        with app.get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET can_manage_customers = 0,
                    can_manage_common_info = 0,
                    can_manage_purchase_followups = 0,
                    can_manage_carton_purchases = 0,
                    can_manage_warehouse_inventory = 0,
                    can_create_products = 0,
                    can_edit_products = 0,
                    can_manage_finance = 0,
                    can_manage_suppliers = 0
                WHERE username = ?
                """,
                (username,),
            )

    def seed_legacy_carton_purchase(self):
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO carton_purchases (
                    id, ordered_at, print_mark, supplier_name, board_type,
                    quantity, unit_price, carton_size, carton_length,
                    carton_width, carton_height, remark, created_at, updated_at
                ) VALUES (
                    73, '2026-09-09', '保密唛头', '保密纸箱厂', 'AB',
                    7, 1234.56, '40x30x20', 40, 30, 20, '按样生产',
                    '2026-09-09T10:00:00', '2026-09-09T10:00:00'
                )
                """
            )

    def test_purchase_view_does_not_grant_purchase_price(self):
        self.login("buyer")
        with app.app.test_request_context("/admin/purchases/raw-material"):
            self.restore_session("buyer")
            self.assertTrue(app.user_has_permission("purchase_view"))
            self.assertFalse(app.user_can_view_purchase_prices())

    def test_legacy_carton_statement_pdf_hides_all_prices_for_price_blind_user(self):
        self.seed_legacy_carton_purchase()
        with app.get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET can_manage_carton_purchases = 1,
                    can_view_purchase_prices = 0,
                    can_manage_finance = 0
                WHERE username = 'buyer'
                """
            )
        self.login("buyer")

        response = self.client.get("/admin/carton-purchases/statement")

        self.assertEqual(response.status_code, 200)
        text = pdf_text(response)
        self.assertIn("纸箱采购对账单", text)
        self.assertIn("保密唛头", text)
        self.assertIn("保密纸箱厂", text)
        for secret in ("单价", "金额", "合计", "1234.56", "8641.92"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)

    def test_legacy_single_carton_purchase_order_pdf_hides_all_prices_for_price_blind_user(self):
        self.seed_legacy_carton_purchase()
        with app.get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET can_manage_carton_purchases = 1,
                    can_view_purchase_prices = 0,
                    can_manage_finance = 0
                WHERE username = 'buyer'
                """
            )
        self.login("buyer")

        response = self.client.get("/admin/carton-purchases/73/purchase-order")

        self.assertEqual(response.status_code, 200)
        text = pdf_text(response)
        self.assertIn("纸箱采购单", text)
        self.assertIn("保密唛头", text)
        self.assertIn("保密纸箱厂", text)
        for secret in ("单价", "总价", "合计", "1234.56", "8641.92"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)

    def test_legacy_carton_pdf_routes_keep_prices_for_price_authorized_user(self):
        self.seed_legacy_carton_purchase()
        with app.get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET can_manage_carton_purchases = 1,
                    can_view_purchase_prices = 1,
                    can_manage_finance = 0
                WHERE username = 'buyer'
                """
            )
        self.login("buyer")

        statement = pdf_text(
            self.client.get("/admin/carton-purchases/statement")
        )
        purchase_order = pdf_text(
            self.client.get("/admin/carton-purchases/73/purchase-order")
        )

        for text in (statement, purchase_order):
            with self.subTest(document=text[:20]):
                self.assertIn("单价", text)
                self.assertIn("1234.56", text)
                self.assertIn("8641.92", text)

    def test_purchase_view_and_manage_only_users_can_reach_consistent_admin_entry(self):
        cases = {
            "view-only": {"can_view_purchases": 1},
            "manage-only": {"can_manage_purchases": 1},
        }
        for username, permissions in cases.items():
            with self.subTest(username=username):
                self.create_procurement_only_user(username, **permissions)
                self.login(username)
                response = self.client.get("/admin")
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                top = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
                self.assertIn('href="/admin">后台</a>', top)
                self.assertIn('href="/admin/purchases/raw-material"', html)
                self.assertIn("统一采购", html)
                self.assertEqual(
                    self.client.get("/admin/purchases/raw-material").status_code,
                    200,
                )

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

    def test_account_edits_grant_all_procurement_permissions_to_admins_only(self):
        client = app.app.test_client()
        with client.session_transaction() as client_session:
            client_session["admin_logged_in"] = True
            client_session["admin_username"] = "admin"

        client.post(
            "/admin/users",
            data={"username": "editable", "password": "password", "role": "operator"},
        )
        with app.get_db() as conn:
            editable_id = conn.execute(
                "SELECT id FROM users WHERE username = 'editable'"
            ).fetchone()["id"]

        client.post(f"/admin/users/{editable_id}/edit", data={"role": "admin"})
        with app.get_db() as conn:
            admin_flags = conn.execute(
                f"SELECT {', '.join(PROCUREMENT_PERMISSION_COLUMNS)} FROM users WHERE id = ?",
                (editable_id,),
            ).fetchone()
        self.assertEqual(tuple(admin_flags), (1, 1, 1, 1, 1, 1, 1, 1))

        client.post(
            f"/admin/users/{editable_id}/edit",
            data={"role": "operator", "can_receive_purchases": "on"},
        )
        with app.get_db() as conn:
            operator_flags = conn.execute(
                f"SELECT {', '.join(PROCUREMENT_PERMISSION_COLUMNS)} FROM users WHERE id = ?",
                (editable_id,),
            ).fetchone()
        self.assertEqual(tuple(operator_flags), (0, 0, 1, 0, 0, 0, 0, 0))


class ProcurementSeededAdminTests(unittest.TestCase):
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

    def test_default_and_configured_seeded_admins_persist_all_procurement_permissions(self):
        with patch.dict(
            os.environ,
            {
                "ADMIN_USERNAME": "seeded-admin",
                "ADMIN_PASSWORD": "seeded-password",
                "ADMIN_ACCOUNTS": "configured-admin:configured-password",
            },
            clear=True,
        ):
            app.init_db()
        with app.get_db() as conn:
            users = conn.execute(
                f"""
                SELECT username, {', '.join(PROCUREMENT_PERMISSION_COLUMNS)}
                FROM users
                WHERE username IN ('seeded-admin', 'configured-admin')
                ORDER BY username
                """
            ).fetchall()
        self.assertEqual(
            [(user["username"], *tuple(user)[1:]) for user in users],
            [
                ("configured-admin", 1, 1, 1, 1, 1, 1, 1, 1),
                ("seeded-admin", 1, 1, 1, 1, 1, 1, 1, 1),
            ],
        )


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

    def test_new_procurement_columns_backfill_exact_legacy_permissions_once(self):
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
            legacy_users = {
                "purchase": (0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0),
                "carton": (0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0),
                "warehouse": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0),
                "powder": (0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0),
                "price": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0),
                "finance": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
                "none": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            }
            for username, permissions in legacy_users.items():
                conn.execute(
                    f"INSERT INTO users ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    (username, "hash", "operator", "2026-09-09T10:00:00", "2026-09-09T10:00:00", *permissions),
                )

        app.init_db()
        expected = {
            "purchase": (1, 1, 0, 0, 0, 0, 0, 1),
            "carton": (1, 1, 1, 0, 0, 0, 0, 1),
            "warehouse": (0, 0, 1, 1, 1, 1, 0, 0),
            "powder": (1, 1, 0, 0, 0, 0, 0, 1),
            "price": (0, 0, 0, 0, 0, 0, 1, 0),
            "finance": (0, 0, 0, 0, 0, 0, 1, 0),
            "none": (0, 0, 0, 0, 0, 0, 0, 0),
        }
        with app.get_db() as conn:
            actual_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            self.assertTrue(set(PROCUREMENT_PERMISSION_COLUMNS) <= actual_columns)
            actual = {
                row["username"]: tuple(row)[1:]
                for row in conn.execute(
                    f"SELECT username, {', '.join(PROCUREMENT_PERMISSION_COLUMNS)} FROM users WHERE username IN ({', '.join('?' for _ in expected)})",
                    tuple(expected),
                )
            }
            conn.execute(
                "UPDATE users SET can_manage_purchases = 0, can_view_purchase_prices = 0 WHERE username = 'carton'"
            )
        self.assertEqual(actual, expected)

        app.init_db()
        with app.get_db() as conn:
            preserved = conn.execute(
                "SELECT can_manage_purchases, can_view_purchase_prices FROM users WHERE username = 'carton'"
            ).fetchone()
        self.assertEqual(tuple(preserved), (0, 0))


if __name__ == "__main__":
    unittest.main()
