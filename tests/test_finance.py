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


class FinanceDomainTestCase(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.init_db()
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            self.customer_a = conn.execute(
                """
                INSERT INTO customers (
                    name, invoice_title, tax_id, registered_address,
                    registered_phone, bank_name, bank_account, invoice_email,
                    created_at, updated_at
                ) VALUES ('客户A', '客户A开票抬头', 'TAX-A', '注册地址A',
                          '0574-A', '银行A', 'ACCOUNT-A', 'a@example.com', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.customer_b = conn.execute(
                "INSERT INTO customers (name, created_at, updated_at) VALUES ('客户B', ?, ?)",
                (now, now),
            ).lastrowid
            self.manual_a = self.create_manual(conn, "FA-100", "财务产品A", "客户A", now)
            self.manual_b = self.create_manual(conn, "FB-100", "财务产品B", "客户B", now)
            order_a = self.create_order(conn, self.manual_a, "SO-A", "客户A", now)
            order_b = self.create_order(conn, self.manual_b, "SO-B", "客户B", now)
            self.ordinary_a_cny = self.create_shipment(
                conn, order_a, 2, 100, "CNY", "2026-09-01", now
            )
            self.ordinary_a_usd = self.create_shipment(
                conn, order_a, 1, 500, "USD", "2026-09-02", now
            )
            self.ordinary_b_cny = self.create_shipment(
                conn, order_b, 3, 200, "CNY", "2026-09-03", now
            )
            self.ordinary_a_unpriced = self.create_shipment(
                conn, order_a, 4, None, "CNY", "2026-09-04", now
            )
            self.batch_a = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户A', 'ASM-A', 4, '2026-09-05', '', 'shipper', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.assembly_a_cny = self.create_assembly_item(
                conn, self.batch_a, self.manual_a, 4, 150, "CNY", now
            )
            self.assembly_a_unpriced = self.create_assembly_item(
                conn, self.batch_a, self.manual_a, 2, None, "CNY", now,
                drawing_no="FA-NULL",
            )

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def create_manual(self, conn, drawing_no, product_name, customer, now):
        return conn.execute(
            """
            INSERT INTO manuals (
                drawing_no, product_name, customer, model, category, version,
                filename, original_filename, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', '', '', '', ?, ?)
            """,
            (drawing_no, product_name, customer, now, now),
        ).lastrowid

    def create_order(self, conn, manual_id, order_no, customer, now):
        return conn.execute(
            """
            INSERT INTO product_orders (
                manual_id, order_no, ordered_at, quantity, customer,
                planned_ship_at, created_at, updated_at
            ) VALUES (?, ?, '2026-09-01', 100, ?, '2026-09-30', ?, ?)
            """,
            (manual_id, order_no, customer, now, now),
        ).lastrowid

    def create_shipment(
        self, conn, order_id, quantity, unit_price_minor, currency, shipped_at, now
    ):
        return conn.execute(
            """
            INSERT INTO product_order_shipments (
                order_id, shipped_quantity, shipped_at, created_at,
                unit_price_minor, currency, price_recorded_by, price_recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'shipper', ?)
            """,
            (
                order_id,
                quantity,
                shipped_at,
                now,
                unit_price_minor,
                currency,
                now if unit_price_minor is not None else "",
            ),
        ).lastrowid

    def create_assembly_item(
        self,
        conn,
        batch_id,
        manual_id,
        quantity,
        unit_price_minor,
        currency,
        now,
        drawing_no="FA-100",
    ):
        return conn.execute(
            """
            INSERT INTO assembly_shipment_items (
                batch_id, manual_id, drawing_no, product_name,
                quantity_per_set, calculated_quantity, shipped_quantity,
                inventory_deducted_quantity, inventory_shortage_quantity,
                unit_price_minor, currency, price_recorded_by, price_recorded_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, '财务组装配件', 1, ?, ?, 0, ?, ?, ?, 'shipper', ?, ?, ?)
            """,
            (
                batch_id,
                manual_id,
                drawing_no,
                quantity,
                quantity,
                quantity,
                unit_price_minor,
                currency,
                now if unit_price_minor is not None else "",
                now,
                now,
            ),
        ).lastrowid


class FinanceSourceTests(FinanceDomainTestCase):
    def test_available_sources_are_normalized_and_exclude_unpriced_and_claimed(self):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            invoice_id = conn.execute(
                """
                INSERT INTO finance_invoices (
                    customer_id, customer_name, currency, total_minor,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, '客户A', 'USD', 500, 'finance', 'finance', ?, ?)
                """,
                (self.customer_a, now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO finance_invoice_items (
                    invoice_id, source_type, source_id, active_claim_key,
                    shipped_at, quantity, unit_price_minor, currency,
                    line_total_minor, created_at
                ) VALUES (?, 'ordinary', ?, ?, '2026-09-02', 1, 500, 'USD', 500, ?)
                """,
                (
                    invoice_id,
                    self.ordinary_a_usd,
                    f"ordinary:{self.ordinary_a_usd}",
                    now,
                ),
            )

        with app.get_db() as conn:
            sources = app.fetch_available_finance_sources(conn, "客户A")

        self.assertEqual(
            {(source["source_type"], source["source_id"]) for source in sources},
            {
                ("ordinary", self.ordinary_a_cny),
                ("assembly_item", self.assembly_a_cny),
            },
        )
        expected_keys = {
            "source_type",
            "source_id",
            "customer_name",
            "order_no",
            "assembly_batch_id",
            "assembly_drawing_no",
            "shipped_at",
            "drawing_no",
            "product_name",
            "quantity",
            "unit_price_minor",
            "currency",
            "line_total_minor",
        }
        for source in sources:
            self.assertEqual(set(source), expected_keys)
            self.assertEqual(source["customer_name"], "客户A")
            self.assertEqual(
                source["line_total_minor"],
                source["quantity"] * source["unit_price_minor"],
            )

        with app.get_db() as conn:
            cny_sources = app.fetch_available_finance_sources(conn, "客户A", "CNY")
            customer_b_sources = app.fetch_available_finance_sources(conn, "客户B")
        self.assertEqual(len(cny_sources), 2)
        self.assertEqual(
            [(source["source_type"], source["source_id"]) for source in customer_b_sources],
            [("ordinary", self.ordinary_b_cny)],
        )

    def test_claim_key_accepts_only_known_source_types(self):
        self.assertEqual(
            app.finance_claim_key("ordinary", self.ordinary_a_cny),
            f"ordinary:{self.ordinary_a_cny}",
        )
        self.assertEqual(
            app.finance_claim_key("assembly_item", self.assembly_a_cny),
            f"assembly_item:{self.assembly_a_cny}",
        )
        with self.assertRaises(ValueError):
            app.finance_claim_key("unknown", 1)


class FinanceInvoiceCreationTests(FinanceDomainTestCase):
    def test_create_invoice_snapshots_sources_customer_and_server_totals(self):
        refs = [
            ("ordinary", self.ordinary_a_cny),
            {"source_type": "assembly_item", "source_id": self.assembly_a_cny},
        ]
        with app.get_db() as conn:
            invoice_id = app.create_finance_invoice(
                conn,
                self.customer_a,
                refs,
                "finance-user",
            )

        with app.get_db() as conn:
            invoice = conn.execute(
                "SELECT * FROM finance_invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            items = conn.execute(
                "SELECT * FROM finance_invoice_items WHERE invoice_id = ? ORDER BY id",
                (invoice_id,),
            ).fetchall()
        self.assertEqual(invoice["status"], "pending")
        self.assertEqual(invoice["currency"], "CNY")
        self.assertEqual(
            invoice["total_minor"],
            sum(item["line_total_minor"] for item in items),
        )
        self.assertEqual(invoice["total_minor"], 800)
        self.assertEqual(invoice["customer_name"], "客户A")
        self.assertEqual(invoice["invoice_title"], "客户A开票抬头")
        self.assertEqual(invoice["tax_id"], "TAX-A")
        self.assertEqual(
            {item["active_claim_key"] for item in items},
            {
                f"ordinary:{self.ordinary_a_cny}",
                f"assembly_item:{self.assembly_a_cny}",
            },
        )

    def test_invalid_source_combinations_are_rejected_with_business_messages(self):
        cases = (
            (
                [
                    ("ordinary", self.ordinary_a_cny),
                    ("ordinary", self.ordinary_b_cny),
                ],
                "只能合并同一客户、同一币种的发货记录",
            ),
            (
                [
                    ("ordinary", self.ordinary_a_cny),
                    ("ordinary", self.ordinary_a_usd),
                ],
                "只能合并同一客户、同一币种的发货记录",
            ),
            (
                [("ordinary", self.ordinary_a_unpriced)],
                "该发货记录尚未记录价格，不能开票",
            ),
            (
                [
                    ("ordinary", self.ordinary_a_cny),
                    ("ordinary", self.ordinary_a_cny),
                ],
                "不能重复选择同一发货记录",
            ),
        )
        for refs, message in cases:
            with self.subTest(message=message):
                with app.get_db() as conn:
                    with self.assertRaisesRegex(ValueError, message):
                        app.create_finance_invoice(
                            conn,
                            self.customer_a,
                            refs,
                            "finance-user",
                        )
                with app.get_db() as conn:
                    count = conn.execute(
                        "SELECT COUNT(*) AS c FROM finance_invoices"
                    ).fetchone()["c"]
                self.assertEqual(count, 0)

    def test_source_cannot_be_claimed_by_two_active_invoices(self):
        refs = [("ordinary", self.ordinary_a_cny)]
        with app.get_db() as conn:
            app.create_finance_invoice(
                conn, self.customer_a, refs, "first-finance-user"
            )

        with app.get_db() as conn:
            with self.assertRaisesRegex(
                ValueError,
                "该发货记录已加入其他开票单",
            ):
                app.create_finance_invoice(
                    conn, self.customer_a, refs, "second-finance-user"
                )


class FinanceInvoiceStateTests(FinanceDomainTestCase):
    def create_invoice(self, refs=None):
        refs = refs or [("ordinary", self.ordinary_a_cny)]
        with app.get_db() as conn:
            return app.create_finance_invoice(
                conn,
                self.customer_a,
                refs,
                "finance-user",
            )

    def invoice_state(self, invoice_id):
        with app.get_db() as conn:
            invoice = conn.execute(
                "SELECT * FROM finance_invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            items = conn.execute(
                "SELECT * FROM finance_invoice_items WHERE invoice_id = ? ORDER BY id",
                (invoice_id,),
            ).fetchall()
        return (dict(invoice) if invoice else None, [dict(row) for row in items])

    def test_pending_items_can_be_replaced_without_rewriting_retained_snapshots(self):
        invoice_id = self.create_invoice()
        _invoice, original_items = self.invoice_state(invoice_id)
        original_item_id = original_items[0]["id"]

        with app.get_db() as conn:
            app.replace_pending_invoice_items(
                conn,
                invoice_id,
                [
                    ("ordinary", self.ordinary_a_cny),
                    ("assembly_item", self.assembly_a_cny),
                ],
                "editor",
            )
        invoice, items = self.invoice_state(invoice_id)
        self.assertEqual(invoice["total_minor"], 800)
        self.assertEqual(invoice["updated_by"], "editor")
        retained = next(item for item in items if item["source_type"] == "ordinary")
        self.assertEqual(retained["id"], original_item_id)

        with app.get_db() as conn:
            app.replace_pending_invoice_items(
                conn,
                invoice_id,
                [("assembly_item", self.assembly_a_cny)],
                "editor-2",
            )
        invoice, items = self.invoice_state(invoice_id)
        self.assertEqual(invoice["total_minor"], 600)
        self.assertEqual(
            [(item["source_type"], item["source_id"]) for item in items],
            [("assembly_item", self.assembly_a_cny)],
        )
        with app.get_db() as conn:
            self.assertFalse(
                app.finance_source_is_claimed(
                    conn, "ordinary", self.ordinary_a_cny
                )
            )

    def test_pending_to_invoiced_paid_reopened_and_void_releases_claims(self):
        invoice_id = self.create_invoice(
            [
                ("ordinary", self.ordinary_a_cny),
                ("assembly_item", self.assembly_a_cny),
            ]
        )
        with app.get_db() as conn:
            app.mark_finance_invoice_invoiced(
                conn,
                invoice_id,
                "INV-2026-001",
                "2026-09-06",
                "首张开票单",
                "issuer",
            )
            app.mark_finance_invoice_paid(
                conn,
                invoice_id,
                "2026-09-10",
                "collector",
            )
            app.reopen_finance_invoice_payment(conn, invoice_id, "reopener")
            app.void_finance_invoice(conn, invoice_id, "voider")

        invoice, items = self.invoice_state(invoice_id)
        self.assertEqual(invoice["status"], "void")
        self.assertEqual(invoice["invoice_no"], "INV-2026-001")
        self.assertEqual(invoice["invoice_date"], "2026-09-06")
        self.assertEqual(invoice["payment_date"], "")
        self.assertEqual(invoice["finance_remark"], "首张开票单")
        self.assertEqual(invoice["voided_by"], "voider")
        self.assertTrue(invoice["voided_at"])
        self.assertTrue(all(item["active_claim_key"] is None for item in items))

    def test_invoiced_can_be_voided_and_pending_can_be_deleted(self):
        invoiced_id = self.create_invoice()
        with app.get_db() as conn:
            app.mark_finance_invoice_invoiced(
                conn,
                invoiced_id,
                "INV-VOID",
                "2026-09-06",
                "",
                "issuer",
            )
            app.void_finance_invoice(conn, invoiced_id, "voider")
        self.assertEqual(self.invoice_state(invoiced_id)[0]["status"], "void")

        pending_id = self.create_invoice([("assembly_item", self.assembly_a_cny)])
        with app.get_db() as conn:
            app.delete_pending_finance_invoice(conn, pending_id)
        self.assertEqual(self.invoice_state(pending_id), (None, []))

    def test_illegal_transitions_and_locked_items_are_rejected(self):
        invoice_id = self.create_invoice()
        with app.get_db() as conn:
            with self.assertRaisesRegex(ValueError, "请填写发票号码和开票日期"):
                app.mark_finance_invoice_invoiced(
                    conn, invoice_id, "", "", "", "issuer"
                )
            with self.assertRaisesRegex(ValueError, "只有已开票记录可以登记收款"):
                app.mark_finance_invoice_paid(
                    conn, invoice_id, "2026-09-10", "collector"
                )
            app.mark_finance_invoice_invoiced(
                conn,
                invoice_id,
                "INV-LOCKED",
                "2026-09-06",
                "",
                "issuer",
            )
            with self.assertRaisesRegex(ValueError, "只有待开票单可以修改明细"):
                app.replace_pending_invoice_items(
                    conn,
                    invoice_id,
                    [("assembly_item", self.assembly_a_cny)],
                    "editor",
                )
            with self.assertRaisesRegex(ValueError, "只有待开票单可以删除"):
                app.delete_pending_finance_invoice(conn, invoice_id)
            app.mark_finance_invoice_paid(
                conn,
                invoice_id,
                "2026-09-10",
                "collector",
            )
            with self.assertRaisesRegex(
                ValueError,
                "已收款记录需先撤销收款后才能作废",
            ):
                app.void_finance_invoice(conn, invoice_id, "voider")

    def test_empty_pending_invoice_cannot_be_marked_invoiced(self):
        invoice_id = self.create_invoice([("ordinary", self.ordinary_a_usd)])
        with app.get_db() as conn:
            app.replace_pending_invoice_items(conn, invoice_id, [], "editor")
            with self.assertRaisesRegex(ValueError, "请至少选择一条发货记录"):
                app.mark_finance_invoice_invoiced(
                    conn,
                    invoice_id,
                    "INV-EMPTY",
                    "2026-09-06",
                    "",
                    "issuer",
                )


class FinanceConcurrencyTests(FinanceDomainTestCase):
    def test_two_connections_cannot_claim_the_same_source(self):
        first = sqlite3.connect(app.DB_PATH, timeout=2)
        second = sqlite3.connect(app.DB_PATH, timeout=2)
        first.row_factory = sqlite3.Row
        second.row_factory = sqlite3.Row
        refs = [("ordinary", self.ordinary_a_cny)]
        try:
            first_invoice_id = app.create_finance_invoice(
                first,
                self.customer_a,
                refs,
                "first",
            )
            first.commit()

            with self.assertRaisesRegex(
                ValueError,
                "该发货记录已加入其他开票单",
            ):
                app.create_finance_invoice(
                    second,
                    self.customer_a,
                    refs,
                    "second",
                )
            second.rollback()
        finally:
            first.close()
            second.close()

        with app.get_db() as conn:
            owners = conn.execute(
                """
                SELECT invoice_id
                FROM finance_invoice_items
                WHERE active_claim_key = ?
                """,
                (f"ordinary:{self.ordinary_a_cny}",),
            ).fetchall()
        self.assertEqual([row["invoice_id"] for row in owners], [first_invoice_id])


class FinanceRouteTests(FinanceDomainTestCase):
    def setUp(self):
        super().setUp()
        self.original_testing = app.app.config["TESTING"]
        self.original_secret_key = app.app.config["SECRET_KEY"]
        app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            self.create_user(conn, "finance-manager", can_manage_finance=1)
            self.create_user(conn, "price-only", can_view_prices=1)
            self.create_user(conn, "unrelated")
        self.client = app.app.test_client()

    def tearDown(self):
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        super().tearDown()

    def create_user(
        self,
        conn,
        username,
        can_view_prices=0,
        can_manage_finance=0,
    ):
        now = "2026-09-03T10:00:00"
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, active,
                can_view_prices, can_manage_finance,
                created_at, updated_at
            ) VALUES (?, 'hash', 'operator', 1, ?, ?, ?, ?)
            """,
            (username, can_view_prices, can_manage_finance, now, now),
        )

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def test_finance_manager_can_create_edit_and_complete_full_lifecycle(self):
        self.login_as("finance-manager")
        response = self.client.get(
            "/admin/finance/new",
            query_string={"customer_id": self.customer_a},
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SO-A", html)
        self.assertIn("ASM-A", html)
        self.assertIn("1.00 CNY", html)
        self.assertNotIn("SO-B", html)
        self.assertNotIn("FA-NULL", html)

        response = self.client.post(
            "/admin/finance/new",
            data={
                "customer_id": str(self.customer_a),
                "source_ref": [
                    f"ordinary:{self.ordinary_a_cny}",
                    f"assembly_item:{self.assembly_a_cny}",
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            invoice = conn.execute(
                "SELECT * FROM finance_invoices ORDER BY id DESC LIMIT 1"
            ).fetchone()
        invoice_id = invoice["id"]
        self.assertEqual(response.location, f"/admin/finance/{invoice_id}")

        detail = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("客户A开票抬头", detail)
        self.assertIn("TAX-A", detail)
        self.assertIn("finance-manager", detail)
        self.assertIn("8.00 CNY", detail)

        response = self.client.post(
            f"/admin/finance/{invoice_id}/items",
            data={"source_ref": f"ordinary:{self.ordinary_a_cny}"},
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            invoice = conn.execute(
                "SELECT total_minor FROM finance_invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
        self.assertEqual(invoice["total_minor"], 200)

        response = self.client.post(
            f"/admin/finance/{invoice_id}/issue",
            data={
                "invoice_no": "INV-ROUTE-001",
                "invoice_date": "2026-09-06",
                "finance_remark": "路由流程",
            },
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/admin/finance/{invoice_id}/pay",
            data={"payment_date": "2026-09-10"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/admin/finance/{invoice_id}/reopen")
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/admin/finance/{invoice_id}/void")
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            invoice = conn.execute(
                "SELECT * FROM finance_invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
        self.assertEqual(invoice["status"], "void")
        self.assertEqual(invoice["invoice_no"], "INV-ROUTE-001")
        self.assertEqual(invoice["finance_remark"], "路由流程")

    def test_invoice_list_filters_customer_status_number_and_date(self):
        with app.get_db() as conn:
            invoice_a = app.create_finance_invoice(
                conn,
                self.customer_a,
                [("ordinary", self.ordinary_a_cny)],
                "finance-manager",
            )
            app.mark_finance_invoice_invoiced(
                conn,
                invoice_a,
                "INV-FILTER-A",
                "2026-09-06",
                "",
                "finance-manager",
            )
            invoice_b = app.create_finance_invoice(
                conn,
                self.customer_b,
                [("ordinary", self.ordinary_b_cny)],
                "finance-manager",
            )
        self.login_as("finance-manager")

        cases = (
            ({"customer": "客户A"}, invoice_a, invoice_b),
            ({"status": "invoiced"}, invoice_a, invoice_b),
            ({"invoice_no": "FILTER-A"}, invoice_a, invoice_b),
            ({"date": "2026-09-06"}, invoice_a, invoice_b),
            ({"status": "pending"}, invoice_b, invoice_a),
        )
        for query, present, absent in cases:
            with self.subTest(query=query):
                response = self.client.get("/admin/finance", query_string=query)
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-finance-invoice-id="{present}"', html)
                self.assertNotIn(f'data-finance-invoice-id="{absent}"', html)

    def test_malformed_and_duplicate_source_references_write_nothing(self):
        self.login_as("finance-manager")
        cases = (
            ["ordinary:not-a-number"],
            [f"ordinary:{self.ordinary_a_cny}", f"ordinary:{self.ordinary_a_cny}"],
            [f"unknown:{self.ordinary_a_cny}"],
        )
        for refs in cases:
            with self.subTest(refs=refs):
                response = self.client.post(
                    "/admin/finance/new",
                    data={"customer_id": str(self.customer_a), "source_ref": refs},
                )
                self.assertEqual(response.status_code, 302)
                with app.get_db() as conn:
                    count = conn.execute(
                        "SELECT COUNT(*) AS c FROM finance_invoices"
                    ).fetchone()["c"]
                self.assertEqual(count, 0)

    def test_every_finance_route_rejects_price_only_and_unrelated_users(self):
        requests = (
            ("get", "/admin/finance", None),
            ("get", "/admin/finance/new", None),
            ("post", "/admin/finance/new", {"customer_id": self.customer_a}),
            ("get", "/admin/finance/999", None),
            ("post", "/admin/finance/999/items", {}),
            ("post", "/admin/finance/999/issue", {}),
            ("post", "/admin/finance/999/pay", {}),
            ("post", "/admin/finance/999/reopen", {}),
            ("post", "/admin/finance/999/void", {}),
            ("post", "/admin/finance/999/delete", {}),
        )
        for username in ("price-only", "unrelated"):
            self.login_as(username)
            for method, path, data in requests:
                with self.subTest(username=username, path=path):
                    response = getattr(self.client, method)(path, data=data)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.location.endswith("/admin"))

    def test_navigation_is_visible_only_to_finance_managers(self):
        self.login_as("finance-manager")
        for path in ("/dashboard", "/admin"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn('href="/admin/finance"', html)
                self.assertIn("财务", html)

        self.login_as("price-only")
        for path in ("/dashboard", "/admin"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertNotIn('href="/admin/finance"', html)


class FinanceShipmentLockTests(FinanceDomainTestCase):
    lock_message = "该发货记录已加入开票单，不能修改发货日期、数量或价格"
    parent_lock_message = "该产品或订单包含已进入财务的发货记录，不能删除"

    def setUp(self):
        super().setUp()
        self.original_testing = app.app.config["TESTING"]
        self.original_secret_key = app.app.config["SECRET_KEY"]
        app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_view_prices, can_manage_finance,
                    can_manage_shipped, can_view_shipped,
                    can_manage_orders, can_view_orders, can_edit_products,
                    created_at, updated_at
                ) VALUES (
                    'finance-manager', 'hash', 'operator', 1,
                    1, 1, 1, 1, 1, 1, 1, ?, ?
                )
                """,
                ("2026-09-03T10:00:00", "2026-09-03T10:00:00"),
            )
        self.client = app.app.test_client()
        self.login_as("finance-manager")

    def tearDown(self):
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        super().tearDown()

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def create_claim(self, source_type, source_id, issued=False):
        with app.get_db() as conn:
            invoice_id = app.create_finance_invoice(
                conn,
                self.customer_a,
                [(source_type, source_id)],
                "finance-manager",
            )
            if issued:
                app.mark_finance_invoice_invoiced(
                    conn,
                    invoice_id,
                    f"INV-LOCK-{invoice_id}",
                    "2026-09-07",
                    "",
                    "finance-manager",
                )
        return invoice_id

    def create_claimable_assembly_batch(self, suffix):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            batch_id = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户A', ?, 4, '2026-09-05', '', 'shipper', ?, ?)
                """,
                (f"ASM-{suffix}", now, now),
            ).lastrowid
            item_id = self.create_assembly_item(
                conn,
                batch_id,
                self.manual_a,
                4,
                150,
                "CNY",
                now,
                drawing_no=f"FA-{suffix}",
            )
        return batch_id, item_id

    def assert_ordinary_source_unchanged(self, source_id, expected_quantity, expected_date):
        with app.get_db() as conn:
            row = conn.execute(
                """
                SELECT shipped_quantity, shipped_at
                FROM product_order_shipments
                WHERE id = ?
                """,
                (source_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            (row["shipped_quantity"], row["shipped_at"]),
            (expected_quantity, expected_date),
        )

    def test_pending_and_issued_ordinary_sources_reject_edit_delete_and_price_backfill(self):
        cases = (
            (self.ordinary_a_cny, False, 2, "2026-09-01"),
            (self.ordinary_a_usd, True, 1, "2026-09-02"),
        )
        for source_id, issued, quantity, shipped_at in cases:
            with self.subTest(issued=issued):
                self.create_claim("ordinary", source_id, issued=issued)

                response = self.client.post(
                    f"/admin/shipped-orders/{source_id}/edit",
                    data={
                        "shipped_quantity": str(quantity),
                        "shipped_at": shipped_at,
                        "logistics_no": "财务锁定后物流备注",
                    },
                    follow_redirects=True,
                )
                self.assertIn("物流备注已更新", response.get_data(as_text=True))
                with app.get_db() as conn:
                    logistics_no = conn.execute(
                        "SELECT logistics_no FROM product_order_shipments WHERE id = ?",
                        (source_id,),
                    ).fetchone()["logistics_no"]
                self.assertEqual(logistics_no, "财务锁定后物流备注")

                response = self.client.post(
                    f"/admin/shipped-orders/{source_id}/edit",
                    data={
                        "shipped_quantity": str(quantity + 1),
                        "shipped_at": "2026-09-20",
                        "logistics_no": "不可改",
                    },
                    follow_redirects=True,
                )
                self.assertIn(self.lock_message, response.get_data(as_text=True))
                self.assert_ordinary_source_unchanged(source_id, quantity, shipped_at)

                response = self.client.post(
                    f"/admin/shipped-orders/{source_id}/delete",
                    follow_redirects=True,
                )
                self.assertIn(self.lock_message, response.get_data(as_text=True))
                self.assert_ordinary_source_unchanged(source_id, quantity, shipped_at)

                with app.get_db() as conn:
                    conn.execute(
                        """
                        UPDATE product_order_shipments
                        SET unit_price_minor = NULL,
                            currency = '',
                            price_recorded_by = '',
                            price_recorded_at = ''
                        WHERE id = ?
                        """,
                        (source_id,),
                    )
                response = self.client.post(
                    f"/admin/shipped-orders/prices/ordinary/{source_id}",
                    data={"unit_price": "9.99", "currency": "CNY"},
                    follow_redirects=True,
                )
                self.assertIn(self.lock_message, response.get_data(as_text=True))
                with app.get_db() as conn:
                    price = conn.execute(
                        "SELECT unit_price_minor FROM product_order_shipments WHERE id = ?",
                        (source_id,),
                    ).fetchone()["unit_price_minor"]
                self.assertIsNone(price)

                response = self.client.post(
                    f"/admin/shipped-orders/{source_id}/remark",
                    data={"remark": "物流备注仍可维护"},
                    follow_redirects=True,
                )
                self.assertIn("发货备注已保存", response.get_data(as_text=True))
                with app.get_db() as conn:
                    remark = conn.execute(
                        "SELECT remark FROM product_order_shipments WHERE id = ?",
                        (source_id,),
                    ).fetchone()["remark"]
                self.assertEqual(remark, "物流备注仍可维护")

    def test_pending_and_issued_assembly_sources_reject_replace_and_delete(self):
        for issued in (False, True):
            with self.subTest(issued=issued):
                batch_id, item_id = self.create_claimable_assembly_batch(
                    "ISSUED" if issued else "PENDING"
                )
                self.create_claim("assembly_item", item_id, issued=issued)

                response = self.client.post(
                    f"/admin/shipped-orders/assembly/{batch_id}/edit",
                    data={
                        "shipped_at": "2026-09-20",
                        "logistics_no": "不可改",
                        "preview_token": "locked-before-preview",
                        "confirm_warnings": "1",
                        "manual_id": str(self.manual_a),
                        "shipped_quantity": "4",
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], self.lock_message)

                response = self.client.post(
                    f"/admin/shipped-orders/assembly/{batch_id}/delete",
                    follow_redirects=True,
                )
                self.assertIn(self.lock_message, response.get_data(as_text=True))
                with app.get_db() as conn:
                    self.assertIsNotNone(
                        conn.execute(
                            "SELECT id FROM assembly_shipment_batches WHERE id = ?",
                            (batch_id,),
                        ).fetchone()
                    )
                    self.assertIsNotNone(
                        conn.execute(
                            "SELECT id FROM assembly_shipment_items WHERE id = ?",
                            (item_id,),
                        ).fetchone()
                    )

                response = self.client.post(
                    f"/admin/shipped-orders/assembly/{batch_id}/edit",
                    data={
                        "shipped_at": "2026-09-05",
                        "logistics_no": "财务锁定后备注",
                        "preview_token": "quantity-is-unchanged",
                        "confirm_warnings": "1",
                        "manual_id": str(self.manual_a),
                        "shipped_quantity": "4",
                    },
                )
                self.assertEqual(response.status_code, 201)
                with app.get_db() as conn:
                    batch = conn.execute(
                        """
                        SELECT shipped_at, logistics_no
                        FROM assembly_shipment_batches
                        WHERE id = ?
                        """,
                        (batch_id,),
                    ).fetchone()
                self.assertEqual(
                    (batch["shipped_at"], batch["logistics_no"]),
                    ("2026-09-05", "财务锁定后备注"),
                )

                with app.get_db() as conn:
                    conn.execute(
                        """
                        UPDATE assembly_shipment_items
                        SET unit_price_minor = NULL,
                            currency = '',
                            price_recorded_by = '',
                            price_recorded_at = ''
                        WHERE id = ?
                        """,
                        (item_id,),
                    )
                response = self.client.post(
                    f"/admin/shipped-orders/prices/assembly_item/{item_id}",
                    data={"unit_price": "8.88", "currency": "CNY"},
                    follow_redirects=True,
                )
                self.assertIn(self.lock_message, response.get_data(as_text=True))
                with app.get_db() as conn:
                    price = conn.execute(
                        "SELECT unit_price_minor FROM assembly_shipment_items WHERE id = ?",
                        (item_id,),
                    ).fetchone()["unit_price_minor"]
                self.assertIsNone(price)

    def test_removing_pending_item_and_voiding_issued_invoice_release_sources(self):
        pending_invoice = self.create_claim("ordinary", self.ordinary_a_cny)
        with app.get_db() as conn:
            app.replace_pending_invoice_items(
                conn,
                pending_invoice,
                [],
                "finance-manager",
            )
        response = self.client.post(
            f"/admin/shipped-orders/{self.ordinary_a_cny}/delete",
            follow_redirects=True,
        )
        self.assertIn("发货记录已删除", response.get_data(as_text=True))

        batch_id, item_id = self.create_claimable_assembly_batch("RELEASE")
        invoice_id = self.create_claim("assembly_item", item_id, issued=True)
        with app.get_db() as conn:
            app.void_finance_invoice(conn, invoice_id, "finance-manager")
        response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/delete",
            follow_redirects=True,
        )
        self.assertIn("组装发货批次已删除", response.get_data(as_text=True))

    def test_order_and_product_delete_are_blocked_before_descendant_sources_change(self):
        self.create_claim("ordinary", self.ordinary_a_cny)
        with app.get_db() as conn:
            order_id = conn.execute(
                "SELECT order_id FROM product_order_shipments WHERE id = ?",
                (self.ordinary_a_cny,),
            ).fetchone()["order_id"]
        response = self.client.post(
            f"/admin/orders/{order_id}/delete",
            follow_redirects=True,
        )
        self.assertIn(self.parent_lock_message, response.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertIsNotNone(
                conn.execute("SELECT id FROM product_orders WHERE id = ?", (order_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM product_order_shipments WHERE id = ?",
                    (self.ordinary_a_cny,),
                ).fetchone()
            )

        self.create_claim("assembly_item", self.assembly_a_cny)
        response = self.client.post(
            f"/admin/{self.manual_a}/delete",
            follow_redirects=True,
        )
        self.assertIn(self.parent_lock_message, response.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertIsNotNone(
                conn.execute("SELECT id FROM manuals WHERE id = ?", (self.manual_a,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM assembly_shipment_items WHERE id = ?",
                    (self.assembly_a_cny,),
                ).fetchone()
            )
