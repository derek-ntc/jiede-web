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
