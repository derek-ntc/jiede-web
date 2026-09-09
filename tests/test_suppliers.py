import tempfile
import unittest
from pathlib import Path

import app


class SupplierManagementTests(unittest.TestCase):
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
        self.client = app.app.test_client()
        self.create_user("customer-manager", can_manage_customers=1)
        self.create_user("common-info-manager", can_manage_common_info=1)
        self.create_user("supplier-manager", can_manage_suppliers=1)
        self.create_user("unrelated")

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.app.config.update(
            TESTING=self.original_testing,
            SECRET_KEY=self.original_secret_key,
        )
        self.tmpdir.cleanup()

    def create_user(self, username, **permissions):
        columns = ["username", "password_hash", "role", "active", "created_at", "updated_at"]
        values = [username, "hash", "operator", 1, "2026-09-09T10:00:00", "2026-09-09T10:00:00"]
        for column in (
            "can_manage_customers",
            "can_manage_common_info",
            "can_manage_suppliers",
        ):
            columns.append(column)
            values.append(permissions.get(column, 0))
        with app.get_db() as conn:
            conn.execute(
                f"INSERT INTO users ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )

    def login_as(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    def supplier_data(self, name="供方甲", **overrides):
        data = {
            "code": "",
            "name": name,
            "contact": "李四",
            "phone": " 0574-1234 ",
            "email": " sales@example.com ",
            "address": "宁波市地址 1 号",
            "remark": "长期合作",
        }
        data.update(overrides)
        return data

    def create_supplier(self, name="供方甲", **overrides):
        self.login_as("supplier-manager")
        response = self.client.post(
            "/admin/business-partners/suppliers",
            data=self.supplier_data(name, **overrides),
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            return conn.execute("SELECT id FROM suppliers WHERE name = ?", (name.strip(),)).fetchone()["id"]

    def create_purchase_order(self, supplier_id):
        with app.get_db() as conn:
            supplier = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO purchase_orders (
                    order_no, category, supplier_id, supplier_code, supplier_name,
                    supplier_contact, supplier_phone, supplier_email, supplier_address,
                    purchased_at, delivery_address, recipient, recipient_phone, remark,
                    created_by, updated_by, created_at, updated_at
                ) VALUES ('PO-20260909-0001', 'other', ?, ?, ?, ?, ?, ?, ?,
                          '2026-09-09', '', '', '', '', 'supplier-manager', 'supplier-manager',
                          '2026-09-09T10:00:00', '2026-09-09T10:00:00')
                """,
                (
                    supplier_id,
                    supplier["code"],
                    supplier["name"],
                    supplier["contact"],
                    supplier["phone"],
                    supplier["email"],
                    supplier["address"],
                ),
            )

    def create_profile(self, name, default=False):
        self.login_as("supplier-manager")
        response = self.client.post(
            "/admin/business-partners/purchase-delivery-profiles",
            data={
                "name": name,
                "delivery_address": f"{name}地址",
                "recipient": "收货人",
                "phone": "0574-0000",
                "default_remark": "工作日送达",
                "is_default": "on" if default else "",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            return conn.execute(
                "SELECT id FROM purchase_delivery_profiles WHERE name = ?", (name,)
            ).fetchone()["id"]

    def profile_data(self, name, default=False):
        return {
            "name": name,
            "delivery_address": f"{name}地址",
            "recipient": "收货人",
            "phone": "0574-0000",
            "default_remark": "工作日送达",
            "is_default": "on" if default else "",
        }

    def default_profile_ids(self):
        with app.get_db() as conn:
            return [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM purchase_delivery_profiles WHERE active = 1 AND is_default = 1 ORDER BY id"
                )
            ]

    def test_business_partner_customer_alias_keeps_existing_customer_route_canonical(self):
        self.login_as("customer-manager")
        response = self.client.get("/admin/business-partners/customers")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/customers"))
        self.assertEqual(self.client.get("/admin/customers").status_code, 200)

    def test_business_partner_common_info_alias_keeps_existing_common_info_route_canonical(self):
        self.login_as("common-info-manager")
        response = self.client.get("/admin/business-partners/common-info")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/common-info"))
        self.assertEqual(self.client.get("/admin/common-info").status_code, 200)

    def test_supplier_routes_require_supplier_manage_permission(self):
        self.login_as("unrelated")
        for url in (
            "/admin/business-partners/suppliers",
            "/admin/business-partners/purchase-delivery-profiles",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/admin"))

    def test_supplier_only_user_can_discover_delivery_profile_entry_but_unrelated_user_cannot(self):
        profile_url = "/admin/business-partners/purchase-delivery-profiles"
        self.login_as("supplier-manager")
        supplier_page = self.client.get("/admin/business-partners/suppliers")
        self.assertEqual(supplier_page.status_code, 200)
        self.assertIn(f'href="{profile_url}"', supplier_page.get_data(as_text=True))

        self.login_as("unrelated")
        self.assertEqual(self.client.get(profile_url).status_code, 302)
        self.assertNotIn(profile_url, self.client.get("/admin").get_data(as_text=True))

    def test_supplier_create_normalizes_fields_and_generates_first_available_code(self):
        supplier_id = self.create_supplier("  供方甲  ")
        with app.get_db() as conn:
            supplier = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        self.assertEqual(supplier["code"], "SUP-00001")
        self.assertEqual(supplier["name"], "供方甲")
        self.assertEqual(supplier["phone"], "0574-1234")
        self.assertEqual(supplier["email"], "sales@example.com")
        self.assertEqual(
            app.supplier_snapshot(supplier),
            {
                "supplier_code": "SUP-00001",
                "supplier_name": "供方甲",
                "supplier_contact": "李四",
                "supplier_phone": "0574-1234",
                "supplier_email": "sales@example.com",
                "supplier_address": "宁波市地址 1 号",
            },
        )

    def test_supplier_rejects_duplicate_trimmed_code_and_name_without_partial_write(self):
        self.create_supplier("供方甲", code=" SUP-99 ")
        self.login_as("supplier-manager")
        duplicate_code = self.client.post(
            "/admin/business-partners/suppliers",
            data=self.supplier_data("供方乙", code="SUP-99"),
            follow_redirects=True,
        )
        duplicate_name = self.client.post(
            "/admin/business-partners/suppliers",
            data=self.supplier_data("  供方甲  ", code="SUP-100"),
            follow_redirects=True,
        )
        self.assertIn("供应商编码已存在", duplicate_code.get_data(as_text=True))
        self.assertIn("供应商名称已存在", duplicate_name.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0], 1)

    def test_supplier_edit_rejects_blank_code_without_changing_existing_supplier(self):
        supplier_id = self.create_supplier("供方甲", code="SUP-001")
        response = self.client.post(
            f"/admin/business-partners/suppliers/{supplier_id}/edit",
            data=self.supplier_data("改名失败", code=""),
            follow_redirects=True,
        )
        self.assertIn("供应商编码为必填项", response.get_data(as_text=True))
        with app.get_db() as conn:
            supplier = conn.execute("SELECT code, name FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        self.assertEqual((supplier["code"], supplier["name"]), ("SUP-001", "供方甲"))

    def test_used_supplier_is_deactivated_not_deleted(self):
        supplier_id = self.create_supplier("供方A")
        self.create_purchase_order(supplier_id)
        response = self.client.post(f"/admin/business-partners/suppliers/{supplier_id}/delete")
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()[0], 0)

    def test_legacy_linked_supplier_without_orders_stays_deactivated_across_startup(self):
        supplier_id = self.create_supplier("历史纸箱供方")
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO carton_suppliers (
                    id, name, contact, phone, remark, created_at, updated_at
                ) VALUES (
                    91, '历史纸箱供方', '旧联系人', '0574-0091', '',
                    '2026-09-09T10:00:00', '2026-09-09T10:00:00'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO supplier_legacy_links (
                    supplier_id, legacy_source, legacy_id
                ) VALUES (?, 'carton_suppliers', 91)
                """,
                (supplier_id,),
            )

        response = self.client.post(
            f"/admin/business-partners/suppliers/{supplier_id}/delete"
        )
        self.assertEqual(response.status_code, 302)
        app.init_db()

        with app.get_db() as conn:
            supplier = conn.execute(
                "SELECT id, active FROM suppliers WHERE id = ?", (supplier_id,)
            ).fetchone()
            link = conn.execute(
                """
                SELECT supplier_id FROM supplier_legacy_links
                WHERE legacy_source = 'carton_suppliers' AND legacy_id = 91
                """
            ).fetchone()
            self.assertEqual(tuple(supplier), (supplier_id, 0))
            self.assertEqual(link["supplier_id"], supplier_id)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM suppliers WHERE trim(name) = '历史纸箱供方'"
                ).fetchone()[0],
                1,
            )

    def test_unused_supplier_is_hard_deleted_and_inactive_supplier_can_be_reactivated(self):
        unused_id = self.create_supplier("未使用供方")
        response = self.client.post(f"/admin/business-partners/suppliers/{unused_id}/delete")
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM suppliers WHERE id=?", (unused_id,)).fetchone())

        used_id = self.create_supplier("可恢复供方")
        self.create_purchase_order(used_id)
        self.client.post(f"/admin/business-partners/suppliers/{used_id}/delete")
        response = self.client.post(f"/admin/business-partners/suppliers/{used_id}/reactivate")
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT active FROM suppliers WHERE id=?", (used_id,)).fetchone()[0], 1)

    def test_only_one_delivery_profile_is_default(self):
        first = self.create_profile("一厂", default=True)
        second = self.create_profile("二厂", default=True)
        self.assertEqual(self.default_profile_ids(), [second])
        self.assertNotEqual(first, second)

    def test_inactive_profile_cannot_become_default_or_conflict_on_reactivation(self):
        active_default = self.create_profile("默认 A", default=True)
        inactive = self.create_profile("停用 B")
        self.assertEqual(
            self.client.post(
                f"/admin/business-partners/purchase-delivery-profiles/{inactive}/delete"
            ).status_code,
            302,
        )

        edit_response = self.client.post(
            f"/admin/business-partners/purchase-delivery-profiles/{inactive}/edit",
            data=self.profile_data("停用 B", default=True),
            follow_redirects=True,
        )
        self.assertIn("停用的收货模板不能设为默认", edit_response.get_data(as_text=True))
        reactivation = self.client.post(
            f"/admin/business-partners/purchase-delivery-profiles/{inactive}/reactivate"
        )
        self.assertNotEqual(reactivation.status_code, 500)
        self.assertEqual(reactivation.status_code, 302)
        self.assertEqual(self.default_profile_ids(), [active_default])
        with app.get_db() as conn:
            profile = conn.execute(
                "SELECT active, is_default FROM purchase_delivery_profiles WHERE id = ?", (inactive,)
            ).fetchone()
        self.assertEqual((profile["active"], profile["is_default"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
