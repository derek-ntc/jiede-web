import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from pricing import line_total_minor


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


class ShipmentPriceSnapshotTests(unittest.TestCase):
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
                    username, password_hash, role, active,
                    can_manage_shipped, can_view_shipped, can_view_prices,
                    created_at, updated_at
                ) VALUES ('shipper', 'hash', 'operator', 1, 1, 1, 0, ?, ?)
                """,
                (now, now),
            )
            self.location_id = conn.execute(
                """
                INSERT INTO warehouse_locations (
                    name, code, remark, enabled, created_at, updated_at
                ) VALUES ('价格快照库位', 'PRICE-SNAPSHOT', '', 1, ?, ?)
                """,
                (now, now),
            ).lastrowid
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "shipper"
            session["admin_role"] = "operator"

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        self.tmpdir.cleanup()

    def create_order(self, drawing_no, unit_price_minor):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, model, category, version,
                    filename, original_filename, unit_price_minor, currency,
                    created_at, updated_at
                ) VALUES (?, ?, '客户甲', '', '', '', '', '', ?, 'CNY', ?, ?)
                """,
                (drawing_no, f"产品-{drawing_no}", unit_price_minor, now, now),
            ).lastrowid
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    planned_ship_at, created_at, updated_at
                ) VALUES (?, ?, '2026-09-03', 10, '客户甲', '2026-09-04', ?, ?)
                """,
                (manual_id, f"SO-{drawing_no}", now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO inventory_balances (
                    manual_id, location_id, quantity, updated_at
                ) VALUES (?, ?, 10, ?)
                """,
                (manual_id, self.location_id, now),
            )
        return manual_id, order_id

    def ship(self, order_id, quantity, **extra_data):
        return self.client.post(
            "/admin/shipped-orders/new",
            data={
                "order_id": str(order_id),
                "shipped_quantity": str(quantity),
                "shipped_at": "2026-09-03",
                "logistics_no": "SNAPSHOT",
                **extra_data,
            },
        )

    def test_ordinary_shipment_snapshots_server_price_and_edit_keeps_it_immutable(self):
        manual_id, order_id = self.create_order("P-250", 250)

        response = self.ship(
            order_id,
            4,
            unit_price_minor="1",
            currency="USD",
            price_recorded_by="browser",
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 999 WHERE id = ?",
                (manual_id,),
            )
        edit_response = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/edit",
            data={
                "shipped_quantity": "4",
                "shipped_at": "2026-09-04",
                "logistics_no": "EDITED",
                "unit_price_minor": "999",
                "currency": "USD",
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE id = ?",
                (shipment["id"],),
            ).fetchone()

        self.assertEqual(
            (shipment["unit_price_minor"], shipment["currency"]),
            (250, "CNY"),
        )
        self.assertEqual(line_total_minor(shipment["unit_price_minor"], 4), 1000)
        self.assertEqual(shipment["price_recorded_by"], "shipper")
        self.assertTrue(shipment["price_recorded_at"])

    def test_ordinary_shipment_snapshots_zero_as_a_recorded_price(self):
        _manual_id, order_id = self.create_order("P-ZERO", 0)

        response = self.ship(order_id, 2)

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(
            (
                shipment["unit_price_minor"],
                shipment["currency"],
                shipment["price_recorded_by"],
                bool(shipment["price_recorded_at"]),
            ),
            (0, "CNY", "shipper", True),
        )

    def test_ordinary_shipment_keeps_null_price_and_blank_audit_fields(self):
        _manual_id, order_id = self.create_order("P-NULL", None)

        response = self.ship(
            order_id,
            2,
            unit_price_minor="777",
            currency="USD",
            price_recorded_at="browser",
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(
            (
                shipment["unit_price_minor"],
                shipment["currency"],
                shipment["price_recorded_by"],
                shipment["price_recorded_at"],
            ),
            (None, "CNY", "", ""),
        )


class ShipmentPriceTestCase(unittest.TestCase):
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
            self.create_user(conn, "viewer", can_view_shipped=1)
            self.create_user(
                conn,
                "price-viewer",
                can_view_shipped=1,
                can_view_prices=1,
            )
            self.create_user(conn, "shipper", can_manage_shipped=1)
            self.create_user(
                conn,
                "price-shipper",
                can_manage_shipped=1,
                can_view_prices=1,
            )
            self.manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, model, category, version,
                    filename, original_filename, unit_price_minor, currency,
                    created_at, updated_at
                ) VALUES ('PRICE-100', '价格测试产品', '客户甲', '', '', '', '', '',
                          9999, 'USD', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    planned_ship_at, created_at, updated_at
                ) VALUES (?, 'SO-PRICE-100', '2026-09-01', 20, '客户甲',
                          '2026-09-05', ?, ?)
                """,
                (self.manual_id, now, now),
            ).lastrowid
            self.priced_shipment_id = conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at,
                    unit_price_minor, currency, price_recorded_by, price_recorded_at
                ) VALUES (?, 2, '2026-09-02', ?, 850, 'CNY', 'seed', ?)
                """,
                (self.order_id, now, now),
            ).lastrowid
            self.unpriced_shipment_id = conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at
                ) VALUES (?, 3, '2026-09-03', ?)
                """,
                (self.order_id, now),
            ).lastrowid
            self.batch_id = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户甲', 'ASM-PRICE', 3, '2026-09-03', '', 'seed', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.priced_assembly_item_id = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    unit_price_minor, currency, price_recorded_by, price_recorded_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'PRICE-100', '价格测试产品', 1, 3, 3, 0, 3,
                          425, 'CNY', 'seed', ?, ?, ?)
                """,
                (self.batch_id, self.manual_id, now, now, now),
            ).lastrowid
            self.unpriced_assembly_item_id = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    created_at, updated_at
                ) VALUES (?, ?, 'PRICE-NULL', '未定价配件', 1, 3, 3, 0, 3, ?, ?)
                """,
                (self.batch_id, self.manual_id, now, now),
            ).lastrowid
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        self.tmpdir.cleanup()

    def create_user(
        self,
        conn,
        username,
        can_manage_shipped=0,
        can_view_shipped=0,
        can_view_prices=0,
    ):
        now = "2026-09-03T10:00:00"
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, active,
                can_manage_shipped, can_view_shipped, can_view_prices,
                created_at, updated_at
            ) VALUES (?, 'hash', 'operator', 1, ?, ?, ?, ?, ?)
            """,
            (
                username,
                can_manage_shipped,
                can_view_shipped,
                can_view_prices,
                now,
                now,
            ),
        )

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def capture_request_sql(self, request):
        statements = []
        sqlite_connect = app.sqlite3.connect

        def traced_connect(*args, **kwargs):
            conn = sqlite_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(app.sqlite3, "connect", side_effect=traced_connect):
            response = request()
        return response, statements


class ShipmentPriceVisibilityTests(ShipmentPriceTestCase):
    def test_price_reader_sees_snapshot_prices_totals_and_missing_state(self):
        self.login_as("price-viewer")

        response = self.client.get("/admin/shipped-orders")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("发货单价", html)
        self.assertIn("发货金额", html)
        self.assertIn("8.50 CNY", html)
        self.assertIn("17.00 CNY", html)
        self.assertIn("4.25 CNY", html)
        self.assertIn("12.75 CNY", html)
        self.assertIn("未记录价格", html)
        self.assertNotIn("/admin/shipped-orders/prices/", html)

        self.login_as("price-shipper")
        manager_html = self.client.get("/admin/shipped-orders").get_data(as_text=True)
        self.assertIn(
            f"/admin/shipped-orders/prices/ordinary/{self.unpriced_shipment_id}",
            manager_html,
        )
        self.assertIn(
            f"/admin/shipped-orders/prices/assembly_item/{self.unpriced_assembly_item_id}",
            manager_html,
        )

    def test_unauthorized_page_neither_selects_nor_renders_price_data(self):
        self.login_as("viewer")

        response, statements = self.capture_request_sql(
            lambda: self.client.get("/admin/shipped-orders")
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        price_selects = [
            " ".join(statement.lower().split())
            for statement in statements
            if statement.lstrip().lower().startswith("select")
            # Startup reads CREATE TABLE definitions to verify migrations;
            # those schema reads do not select finance/customer price records.
            and not statement.lstrip().lower().startswith("select sql from sqlite_master")
        ]
        self.assertTrue(price_selects)
        for statement in price_selects:
            self.assertNotIn("unit_price_minor", statement)
            self.assertNotRegex(statement, r"\bcurrency\b")
            self.assertNotIn("finance_", statement)
        self.assertNotIn("发货单价", html)
        self.assertNotIn("发货金额", html)
        self.assertNotIn("8.50 CNY", html)
        self.assertNotIn("未记录价格", html)

    def test_public_signature_and_photo_pages_keep_price_columns_out_of_queries(self):
        signature_token = "signature_token_1234567890"
        photo_token = "photo_token_123456789012345"
        with app.get_db() as conn:
            conn.execute(
                """
                UPDATE product_order_shipments
                SET signature_token = ?, signature_expires_at = '2099-09-03T10:00:00',
                    photo_upload_token = ?
                WHERE id = ?
                """,
                (signature_token, photo_token, self.priced_shipment_id),
            )
        with self.client.session_transaction() as session:
            session.clear()

        for path in (f"/sign/{signature_token}", f"/shipment-photos/{photo_token}"):
            with self.subTest(path=path):
                response, statements = self.capture_request_sql(
                    lambda path=path: self.client.get(path)
                )
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                for statement in statements:
                    normalized = " ".join(statement.lower().split())
                    if normalized.startswith("select") and not normalized.startswith("select sql from sqlite_master"):
                        self.assertNotIn("unit_price_minor", normalized)
                        self.assertNotRegex(normalized, r"\bcurrency\b")
                        self.assertNotIn("finance_", normalized)
                self.assertNotIn("8.50 CNY", html)


class ShipmentPriceBackfillTests(ShipmentPriceTestCase):
    def post_price(self, username, source_type, source_id, unit_price, currency="CNY"):
        self.login_as(username)
        return self.client.post(
            f"/admin/shipped-orders/prices/{source_type}/{source_id}",
            data={"unit_price": unit_price, "currency": currency},
        )

    def test_backfill_requires_both_price_visibility_and_shipment_management(self):
        for username in ("price-viewer", "shipper"):
            with self.subTest(username=username):
                response = self.post_price(
                    username,
                    "ordinary",
                    self.unpriced_shipment_id,
                    "8.50",
                )
                self.assertEqual(response.status_code, 302)
                with app.get_db() as conn:
                    row = conn.execute(
                        "SELECT unit_price_minor FROM product_order_shipments WHERE id = ?",
                        (self.unpriced_shipment_id,),
                    ).fetchone()
                self.assertIsNone(row["unit_price_minor"])

    def test_backfills_ordinary_and_zero_assembly_prices_with_audit_only(self):
        response = self.post_price(
            "price-shipper",
            "ordinary",
            self.unpriced_shipment_id,
            "8.50",
        )
        self.assertEqual(response.status_code, 302)
        response = self.post_price(
            "price-shipper",
            "assembly_item",
            self.unpriced_assembly_item_id,
            "0",
            "JPY",
        )
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE id = ?",
                (self.unpriced_shipment_id,),
            ).fetchone()
            item = conn.execute(
                "SELECT * FROM assembly_shipment_items WHERE id = ?",
                (self.unpriced_assembly_item_id,),
            ).fetchone()
            manual = conn.execute(
                "SELECT unit_price_minor, currency FROM manuals WHERE id = ?",
                (self.manual_id,),
            ).fetchone()

        self.assertEqual(
            (
                shipment["unit_price_minor"],
                shipment["currency"],
                shipment["price_recorded_by"],
                bool(shipment["price_recorded_at"]),
                shipment["shipped_quantity"],
            ),
            (850, "CNY", "price-shipper", True, 3),
        )
        self.assertEqual(
            (
                item["unit_price_minor"],
                item["currency"],
                item["price_recorded_by"],
                bool(item["price_recorded_at"]),
                item["shipped_quantity"],
            ),
            (0, "JPY", "price-shipper", True, 3),
        )
        self.assertEqual(
            (manual["unit_price_minor"], manual["currency"]),
            (9999, "USD"),
        )

    def test_invalid_unknown_and_existing_prices_are_not_overwritten(self):
        cases = (
            ("ordinary", self.unpriced_shipment_id, "-0.01", "CNY"),
            ("ordinary", self.unpriced_shipment_id, "1.00", "XYZ"),
            ("unknown", self.unpriced_shipment_id, "1.00", "CNY"),
            ("ordinary", self.priced_shipment_id, "1.00", "CNY"),
        )
        for source_type, source_id, unit_price, currency in cases:
            with self.subTest(source_type=source_type, unit_price=unit_price, currency=currency):
                response = self.post_price(
                    "price-shipper",
                    source_type,
                    source_id,
                    unit_price,
                    currency,
                )
                self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            unpriced = conn.execute(
                "SELECT unit_price_minor FROM product_order_shipments WHERE id = ?",
                (self.unpriced_shipment_id,),
            ).fetchone()
            priced = conn.execute(
                "SELECT unit_price_minor, currency FROM product_order_shipments WHERE id = ?",
                (self.priced_shipment_id,),
            ).fetchone()
        self.assertIsNone(unpriced["unit_price_minor"])
        self.assertEqual((priced["unit_price_minor"], priced["currency"]), (850, "CNY"))


class ProductPageTestCase(unittest.TestCase):
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
            self.manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, supplier, customer,
                    pack_quantity, pack_carton_size, pack_weight,
                    sku, barcode, min_stock, remark, description_html,
                    unit_price_minor, currency, model, category, version,
                    filename, original_filename, created_at, updated_at
                ) VALUES (
                    'P-100', '分栏产品', '供应商甲', '客户甲',
                    '40PCS', '31x31x31.5CM', '12KG',
                    'SKU-100', 'BAR-100', 5, '基本备注', '<p>装配步骤甲</p>',
                    1234, 'CNY', '', '', '', '', '', ?, ?
                )
                """,
                (now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO product_materials (
                    manual_id, material, thickness, surface_type, supplier,
                    created_at, updated_at
                ) VALUES (?, 'Q235', '1.2mm', '喷粉', '材料商甲', ?, ?)
                """,
                (self.manual_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO manual_inspection_requirements (
                    manual_id, item_name, standard, method, remark,
                    sort_order, created_at, updated_at
                ) VALUES (?, '外观', '无划痕', '目视', '', 0, ?, ?)
                """,
                (self.manual_id, now, now),
            )
            self.file_ids = [
                conn.execute(
                    """
                    INSERT INTO manual_files (
                        manual_id, filename, original_filename, file_type, created_at
                    ) VALUES (?, ?, ?, 'file', ?)
                    """,
                    (self.manual_id, filename, original_filename, now),
                ).lastrowid
                for filename, original_filename in (
                    ("drawing-a.dwg", "图纸甲.dwg"),
                    ("drawing-b.dwg", "图纸乙.dwg"),
                )
            ]
            conn.execute(
                """
                UPDATE manuals
                SET filename = 'drawing-a.dwg', original_filename = '图纸甲.dwg', file_type = 'file'
                WHERE id = ?
                """,
                (self.manual_id,),
            )
            self.create_user(conn, "price-editor", can_edit_products=1, can_view_prices=1)
            self.create_user(conn, "plain-editor", can_edit_products=1)
            self.create_user(conn, "price-reader", can_view_prices=1)
            self.create_user(conn, "price-creator", can_create_products=1, can_view_prices=1)
            self.create_user(conn, "plain-creator", can_create_products=1)

        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(TESTING=self.original_testing, SECRET_KEY=self.original_secret_key)
        self.tmpdir.cleanup()

    def create_user(
        self,
        conn,
        username,
        can_edit_products=0,
        can_create_products=0,
        can_view_prices=0,
        can_manage_warehouse_inventory=0,
    ):
        now = "2026-09-03T10:00:00"
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, active,
                can_manage_products, can_create_products, can_edit_products,
                can_view_prices, can_manage_finance,
                can_manage_warehouse_inventory, created_at, updated_at
            ) VALUES (?, 'hash', 'operator', 1, 0, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                username,
                can_create_products,
                can_edit_products,
                can_view_prices,
                can_manage_warehouse_inventory,
                now,
                now,
            ),
        )

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def valid_basic_form(self, **overrides):
        data = {
            "drawing_no": "P-100",
            "product_name": "分栏产品",
            "supplier": "供应商甲",
            "customer": "客户甲",
            "pack_quantity": "40PCS",
            "pack_carton_size": "31x31x31.5CM",
            "pack_weight": "12KG",
            "sku": "SKU-100",
            "barcode": "BAR-100",
            "default_location_id": "",
            "min_stock": "5",
            "remark": "基本备注",
        }
        data.update(overrides)
        return data

    def capture_request_sql(self, request):
        statements = []
        sqlite_connect = app.sqlite3.connect

        def traced_connect(*args, **kwargs):
            conn = sqlite_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(app.sqlite3, "connect", side_effect=traced_connect):
            response = request()
        return response, statements

    def assert_manual_selects_exclude_price(self, statements):
        manual_selects = []
        for statement in statements:
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select") and " from manuals" in normalized:
                manual_selects.append(normalized)

        self.assertTrue(manual_selects, statements)
        for statement in manual_selects:
            self.assertNotIn("unit_price_minor", statement)
            self.assertNotRegex(statement, r"\bcurrency\b")
            self.assertNotRegex(statement, r"select\s+(?:manuals\.)?\*")


class ProductPageSplitTests(ProductPageTestCase):
    def test_public_basic_and_technical_pages_show_only_their_owned_sections(self):
        response = self.client.get(f"/manual/{self.manual_id}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("基本信息", html)
        self.assertNotIn("作业指导书", html)
        self.assertNotIn("装配步骤甲", html)
        self.assertNotIn("Q235", html)

        response = self.client.get(f"/manual/{self.manual_id}/technical")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("技术资料", html)
        self.assertIn("作业指导书", html)
        self.assertIn("装配步骤甲", html)
        self.assertIn("Q235", html)
        self.assertNotIn("基本备注", html)

    def test_edit_pages_show_only_fields_owned_by_the_active_tab(self):
        self.login_as("plain-editor")

        basic_html = self.client.get(
            f"/admin/{self.manual_id}/edit"
        ).get_data(as_text=True)
        self.assertIn("基本信息", basic_html)
        self.assertIn('name="product_name"', basic_html)
        self.assertIn('name="assembly_drawing_no"', basic_html)
        self.assertNotIn('name="description_html"', basic_html)
        self.assertNotIn('name="material"', basic_html)
        self.assertNotIn('name="uploads"', basic_html)

        technical_html = self.client.get(
            f"/admin/{self.manual_id}/edit/technical"
        ).get_data(as_text=True)
        self.assertIn("技术资料", technical_html)
        self.assertIn('name="description_html"', technical_html)
        self.assertIn('name="material"', technical_html)
        self.assertIn('name="uploads"', technical_html)
        self.assertNotIn('name="product_name"', technical_html)
        self.assertNotIn('name="assembly_drawing_no"', technical_html)

    def test_each_edit_post_updates_only_fields_owned_by_its_tab(self):
        self.login_as("plain-editor")

        response = self.client.post(
            f"/admin/{self.manual_id}/edit",
            data=self.valid_basic_form(
                product_name="基础更新产品",
                description_html="<p>不应覆盖技术资料</p>",
                material="不应覆盖材料",
            ),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            manual = conn.execute(
                "SELECT product_name, description_html FROM manuals WHERE id = ?",
                (self.manual_id,),
            ).fetchone()
            materials = app.get_product_materials(conn, self.manual_id)
        self.assertEqual(manual["product_name"], "基础更新产品")
        self.assertEqual(manual["description_html"], "<p>装配步骤甲</p>")
        self.assertEqual(materials[0]["material"], "Q235")

        response = self.client.post(
            f"/admin/{self.manual_id}/edit/technical",
            data={
                "product_name": "不应覆盖基础信息",
                "description_html": "<p>技术资料已更新</p>",
                "material": ["304"],
                "material_thickness": ["2mm"],
                "surface_type": ["拉丝"],
                "material_supplier": ["材料商乙"],
                "inspection_item_name": ["尺寸"],
                "inspection_standard": ["±0.1mm"],
                "inspection_method": ["卡尺"],
                "inspection_remark": [""],
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            manual = conn.execute(
                "SELECT product_name, description_html FROM manuals WHERE id = ?",
                (self.manual_id,),
            ).fetchone()
            materials = app.get_product_materials(conn, self.manual_id)
        self.assertEqual(manual["product_name"], "基础更新产品")
        self.assertEqual(manual["description_html"], "<p>技术资料已更新</p>")
        self.assertEqual(materials[0]["material"], "304")

    def test_attachment_deletion_redirects_to_technical_edit(self):
        self.login_as("plain-editor")
        response = self.client.post(
            f"/admin/{self.manual_id}/files/{self.file_ids[1]}/delete"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"/admin/{self.manual_id}/edit/technical",
        )


class ProductCurrentPriceTests(ProductPageTestCase):
    def test_create_form_and_write_are_gated_by_price_permission(self):
        self.login_as("plain-creator")
        plain_html = self.client.get("/admin").get_data(as_text=True)
        self.assertNotIn('name="unit_price"', plain_html)
        self.assertNotIn('name="currency"', plain_html)
        response = self.client.post(
            "/admin/upload",
            data=self.valid_basic_form(
                drawing_no="P-PLAIN",
                product_name="无价格权限新产品",
                unit_price="0.01",
                currency="USD",
            ),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            plain_manual = conn.execute(
                "SELECT unit_price_minor, currency FROM manuals WHERE drawing_no = 'P-PLAIN'"
            ).fetchone()
        self.assertEqual(
            (plain_manual["unit_price_minor"], plain_manual["currency"]),
            (None, "CNY"),
        )

        self.login_as("price-creator")
        priced_html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn('name="unit_price"', priced_html)
        self.assertIn('name="currency"', priced_html)
        response = self.client.post(
            "/admin/upload",
            data=self.valid_basic_form(
                drawing_no="P-PRICED",
                product_name="可定价新产品",
                unit_price="12.34",
                currency="CNY",
            ),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            priced_manual = conn.execute(
                "SELECT unit_price_minor, currency FROM manuals WHERE drawing_no = 'P-PRICED'"
            ).fetchone()
        self.assertEqual(
            (priced_manual["unit_price_minor"], priced_manual["currency"]),
            (1234, "CNY"),
        )

    def test_price_projection_is_opt_in(self):
        with app.get_db() as conn:
            safe_manual = app.fetch_manual_by_id(conn, self.manual_id)
            priced_manual = app.fetch_manual_by_id(conn, self.manual_id, include_price=True)

        self.assertNotIn("unit_price_minor", safe_manual.keys())
        self.assertNotIn("currency", safe_manual.keys())
        self.assertEqual(
            (priced_manual["unit_price_minor"], priced_manual["currency"]),
            (1234, "CNY"),
        )

    def test_only_price_readers_see_current_price(self):
        public_html = self.client.get(
            f"/manual/{self.manual_id}"
        ).get_data(as_text=True)
        self.assertNotIn("当前单价", public_html)
        self.assertNotIn("12.34 CNY", public_html)

        self.login_as("plain-editor")
        unauthorized_html = self.client.get(
            f"/admin/{self.manual_id}/edit"
        ).get_data(as_text=True)
        self.assertNotIn("当前单价", unauthorized_html)
        self.assertNotIn("12.34 CNY", unauthorized_html)
        self.assertNotIn('name="unit_price"', unauthorized_html)
        self.assertNotIn('name="currency"', unauthorized_html)

        self.login_as("price-reader")
        reader_html = self.client.get(
            f"/manual/{self.manual_id}"
        ).get_data(as_text=True)
        self.assertIn("当前单价", reader_html)
        self.assertIn("12.34 CNY", reader_html)

    def test_authorized_editor_can_update_current_price(self):
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET unit_price_minor = NULL, currency = 'CNY' WHERE id = ?",
                (self.manual_id,),
            )
        self.login_as("price-editor")
        response = self.client.post(
            f"/admin/{self.manual_id}/edit",
            data=self.valid_basic_form(unit_price="12.34", currency="CNY"),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            manual = conn.execute(
                "SELECT unit_price_minor, currency FROM manuals WHERE id = ?",
                (self.manual_id,),
            ).fetchone()
        self.assertEqual(
            (manual["unit_price_minor"], manual["currency"]),
            (1234, "CNY"),
        )

    def test_warehouse_routes_use_non_price_manual_projections(self):
        with app.get_db() as conn:
            self.create_user(
                conn,
                "warehouse-only",
                can_manage_warehouse_inventory=1,
            )
        self.login_as("warehouse-only")

        response, statements = self.capture_request_sql(
            lambda: self.client.get("/admin/inventory/api/product?code=SKU-100")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["product"]["id"], self.manual_id)
        self.assert_manual_selects_exclude_price(statements)

        for path in (
            "/admin/inventory/inbound",
            "/admin/inventory/outbound",
            "/admin/inventory/adjust",
        ):
            with self.subTest(path=path):
                response, statements = self.capture_request_sql(
                    lambda path=path: self.client.get(path)
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("分栏产品", response.get_data(as_text=True))
                self.assert_manual_selects_exclude_price(statements)

    def test_product_copy_and_delete_use_non_price_manual_projections(self):
        with app.get_db() as conn:
            conn.execute("DELETE FROM manual_files WHERE manual_id = ?", (self.manual_id,))
            conn.execute(
                """
                UPDATE manuals
                SET filename = '', original_filename = '', file_type = 'file'
                WHERE id = ?
                """,
                (self.manual_id,),
            )
        self.login_as("plain-editor")

        response, statements = self.capture_request_sql(
            lambda: self.client.post(f"/admin/{self.manual_id}/copy")
        )
        self.assertEqual(response.status_code, 302)
        self.assert_manual_selects_exclude_price(statements)
        with app.get_db() as conn:
            copied = conn.execute(
                """
                SELECT id, product_name, unit_price_minor, currency
                FROM manuals
                WHERE product_name = '分栏产品 - 副本'
                """
            ).fetchone()
        self.assertIsNotNone(copied)
        self.assertEqual((copied["unit_price_minor"], copied["currency"]), (None, "CNY"))

        response, statements = self.capture_request_sql(
            lambda: self.client.post(f"/admin/{copied['id']}/delete")
        )
        self.assertEqual(response.status_code, 302)
        self.assert_manual_selects_exclude_price(statements)
        with app.get_db() as conn:
            deleted = conn.execute(
                "SELECT id FROM manuals WHERE id = ?",
                (copied["id"],),
            ).fetchone()
        self.assertIsNone(deleted)

    def test_editor_without_price_permission_cannot_alter_price_by_direct_post(self):
        self.login_as("plain-editor")
        response = self.client.post(
            f"/admin/{self.manual_id}/edit",
            data=self.valid_basic_form(unit_price="0.01", currency="USD"),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            manual = conn.execute(
                "SELECT unit_price_minor, currency FROM manuals WHERE id = ?",
                (self.manual_id,),
            ).fetchone()
        self.assertEqual(
            (manual["unit_price_minor"], manual["currency"]),
            (1234, "CNY"),
        )
