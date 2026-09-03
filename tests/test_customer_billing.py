import tempfile
import unittest
from pathlib import Path

import app


class CustomerBillingTests(unittest.TestCase):
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
            self.create_user(conn, "customer-manager", can_manage_customers=1)
            self.create_user(conn, "finance-manager", can_manage_finance=1)
            self.create_user(conn, "unrelated")
            self.customer_id = conn.execute(
                """
                INSERT INTO customers (
                    name, contact, address, email, phone, remark,
                    created_at, updated_at
                ) VALUES ('客户甲', '张三', '送货地址', 'contact@example.com',
                          '0574-12345678', '', ?, ?)
                """,
                (now, now),
            ).lastrowid
            self.other_customer_id = conn.execute(
                """
                INSERT INTO customers (
                    name, invoice_title, tax_id, created_at, updated_at
                ) VALUES ('客户乙', '乙方抬头', 'TAX-BETA', ?, ?)
                """,
                (now, now),
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
        can_manage_customers=0,
        can_manage_finance=0,
    ):
        now = "2026-09-03T10:00:00"
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, active,
                can_manage_customers, can_manage_finance,
                created_at, updated_at
            ) VALUES (?, 'hash', 'operator', 1, ?, ?, ?, ?)
            """,
            (username, can_manage_customers, can_manage_finance, now, now),
        )

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def billing_data(self, **overrides):
        data = {
            "invoice_title": "宁波甲方制造有限公司",
            "tax_id": "91330200MA1234567X",
            "registered_address": "宁波市高新区注册路 1 号",
            "registered_phone": "0574-87654321",
            "bank_name": "中国银行宁波分行",
            "bank_account": "1234567890123456",
            "invoice_email": "invoice@example.com",
        }
        data.update(overrides)
        return data

    def test_customer_manager_can_read_and_save_all_invoice_fields(self):
        self.login_as("customer-manager")
        get_response = self.client.get(
            f"/admin/customers/{self.customer_id}/billing"
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertIn("开票信息", get_response.get_data(as_text=True))

        response = self.client.post(
            f"/admin/customers/{self.customer_id}/billing",
            data=self.billing_data(),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT * FROM customers WHERE id = ?",
                (self.customer_id,),
            ).fetchone()
        self.assertEqual(
            {field: customer[field] for field in app.CUSTOMER_INVOICE_FIELDS},
            self.billing_data(),
        )
        self.assertEqual(
            app.customer_invoice_snapshot(customer),
            {"customer_name": "客户甲", **self.billing_data()},
        )

    def test_finance_can_read_but_only_customer_manager_can_write(self):
        self.login_as("finance-manager")
        response = self.client.get(
            f"/admin/customers/{self.customer_id}/billing"
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("客户甲", html)
        self.assertNotIn("保存开票信息", html)

        response = self.client.post(
            f"/admin/customers/{self.customer_id}/billing",
            data=self.billing_data(invoice_title="不应写入"),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT invoice_title FROM customers WHERE id = ?",
                (self.customer_id,),
            ).fetchone()
        self.assertEqual(customer["invoice_title"], "")

        self.login_as("unrelated")
        response = self.client.get(
            f"/admin/customers/{self.customer_id}/billing"
        )
        self.assertEqual(response.status_code, 302)

    def test_invalid_invoice_email_is_rejected_without_partial_write(self):
        self.login_as("customer-manager")
        response = self.client.post(
            f"/admin/customers/{self.customer_id}/billing",
            data=self.billing_data(invoice_email="not-an-email"),
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("收票邮箱格式不正确", response.get_data(as_text=True))
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT invoice_title, invoice_email FROM customers WHERE id = ?",
                (self.customer_id,),
            ).fetchone()
        self.assertEqual((customer["invoice_title"], customer["invoice_email"]), ("", ""))

    def test_customer_search_includes_invoice_title_and_tax_id(self):
        self.login_as("customer-manager")
        for query in ("乙方抬头", "TAX-BETA"):
            with self.subTest(query=query):
                response = self.client.get(
                    "/admin/customers",
                    query_string={"q": query},
                )
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("客户乙", html)
                self.assertNotIn("客户甲", html)

    def test_customer_with_any_finance_record_cannot_be_deleted(self):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO finance_invoices (
                    customer_id, customer_name, currency, total_minor, status,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, '客户甲', 'CNY', 0, 'void', 'finance-manager',
                          'finance-manager', ?, ?)
                """,
                (self.customer_id, now, now),
            )
        self.login_as("customer-manager")

        response = self.client.post(
            f"/admin/customers/{self.customer_id}/delete",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("该客户已有财务记录，不能删除", response.get_data(as_text=True))
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT id FROM customers WHERE id = ?",
                (self.customer_id,),
            ).fetchone()
        self.assertIsNotNone(customer)

    def test_customer_rename_cascades_live_sources_but_keeps_issued_snapshot(self):
        now = "2026-09-03T10:00:00"
        with app.get_db() as conn:
            manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, model, category, version,
                    filename, original_filename, created_at, updated_at
                ) VALUES ('RENAME-100', '改名测试产品', '客户甲', '', '', '', '', '', ?, ?)
                """,
                (now, now),
            ).lastrowid
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    planned_ship_at, created_at, updated_at
                ) VALUES (?, 'SO-RENAME', '2026-09-01', 2, '客户甲',
                          '2026-09-10', ?, ?)
                """,
                (manual_id, now, now),
            ).lastrowid
            ordinary_id = conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at,
                    unit_price_minor, currency
                ) VALUES (?, 2, '2026-09-03', ?, 100, 'CNY')
                """,
                (order_id, now),
            ).lastrowid
            batch_id = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户甲', 'ASM-RENAME', 1, '2026-09-03', '',
                          'customer-manager', ?, ?)
                """,
                (now, now),
            ).lastrowid
            assembly_item_id = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    unit_price_minor, currency, created_at, updated_at
                ) VALUES (?, ?, 'RENAME-100', '改名测试产品', 1, 1, 1, 0, 1,
                          100, 'CNY', ?, ?)
                """,
                (batch_id, manual_id, now, now),
            ).lastrowid
            issued_invoice_id = conn.execute(
                """
                INSERT INTO finance_invoices (
                    customer_id, customer_name, currency, total_minor, status,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, '客户甲', 'CNY', 0, 'invoiced',
                          'finance-manager', 'finance-manager', ?, ?)
                """,
                (self.customer_id, now, now),
            ).lastrowid
            pending_invoice_id = conn.execute(
                """
                INSERT INTO finance_invoices (
                    customer_id, customer_name, currency, total_minor, status,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, '客户甲', 'CNY', 0, 'pending',
                          'finance-manager', 'finance-manager', ?, ?)
                """,
                (self.customer_id, now, now),
            ).lastrowid

        self.login_as("customer-manager")
        response = self.client.post(
            f"/admin/customers/{self.customer_id}/edit",
            data={
                "name": "客户甲（新名称）",
                "contact": "张三",
                "address": "送货地址",
                "email": "contact@example.com",
                "phone": "0574-12345678",
                "remark": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            live_names = {
                "manual": conn.execute(
                    "SELECT customer FROM manuals WHERE id = ?", (manual_id,)
                ).fetchone()["customer"],
                "order": conn.execute(
                    "SELECT customer FROM product_orders WHERE id = ?", (order_id,)
                ).fetchone()["customer"],
                "assembly": conn.execute(
                    "SELECT customer FROM assembly_shipment_batches WHERE id = ?",
                    (batch_id,),
                ).fetchone()["customer"],
            }
            invoice_names = {
                row["id"]: row["customer_name"]
                for row in conn.execute(
                    "SELECT id, customer_name FROM finance_invoices WHERE id IN (?, ?)",
                    (issued_invoice_id, pending_invoice_id),
                )
            }
            new_sources = app.fetch_available_finance_sources(
                conn, "客户甲（新名称）", "CNY"
            )
            app.sync_product_customers(conn)
            customer_names = {
                row["name"] for row in conn.execute("SELECT name FROM customers")
            }

        self.assertEqual(set(live_names.values()), {"客户甲（新名称）"})
        self.assertEqual(invoice_names[issued_invoice_id], "客户甲")
        self.assertEqual(invoice_names[pending_invoice_id], "客户甲（新名称）")
        self.assertEqual(
            {(source["source_type"], source["source_id"]) for source in new_sources},
            {("ordinary", ordinary_id), ("assembly_item", assembly_item_id)},
        )
        self.assertNotIn("客户甲", customer_names)


if __name__ == "__main__":
    unittest.main()
