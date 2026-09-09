import unittest
from unittest.mock import patch

import app
from tests.test_assembly_shipping import AssemblyAppTestCase
from tests.test_shipped_pdf_company import ShippedPdfCompanyTests


class ShippingWorkflowFieldTests(AssemblyAppTestCase):
    def assembly_save_data(self, preview, *, shipped_at="2026-09-08"):
        return {
            "customer": preview["customer"],
            "assembly_drawing_no": preview["assembly_drawing_no"],
            "set_quantity": str(preview["set_quantity"]),
            "manual_id": [str(item["manual_id"]) for item in preview["items"]],
            "shipped_quantity": [
                str(item["shipped_quantity"]) for item in preview["items"]
            ],
            "shipped_at": shipped_at,
            "logistics_no": "规格快照测试",
            "preview_token": preview["preview_token"],
            "confirm_warnings": "1",
        }

    def test_migration_is_idempotent_and_preserves_product_specification_and_model(self):
        product = self.create_product()
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET supplier='DZ-30型', model='legacy' WHERE id=?",
                (product,),
            )

        app.init_db()
        app.init_db()

        with app.get_db() as conn:
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT supplier, model FROM manuals WHERE id=?", (product,)
                    ).fetchone()
                ),
                ("DZ-30型", "legacy"),
            )
            customer_columns = {
                row["name"]: row for row in conn.execute("PRAGMA table_info(customers)")
            }
            ordinary_columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(product_order_shipments)")
            }
            assembly_columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(assembly_shipment_items)")
            }

        self.assertEqual(customer_columns["recipient_name"]["notnull"], 1)
        self.assertEqual(customer_columns["recipient_name"]["dflt_value"], "''")
        self.assertEqual(customer_columns["recipient_phone"]["notnull"], 1)
        self.assertEqual(customer_columns["recipient_phone"]["dflt_value"], "''")
        self.assertEqual(ordinary_columns["specification_snapshot"]["notnull"], 0)
        self.assertEqual(assembly_columns["specification_snapshot"]["notnull"], 0)

        pages = [
            self.client.get("/admin/products").get_data(as_text=True),
            self.client.get("/admin").get_data(as_text=True),
            self.client.get(f"/admin/{product}/edit").get_data(as_text=True),
            self.client.get(f"/manual/{product}").get_data(as_text=True),
        ]
        for html in pages:
            self.assertIn("规格型号", html)
        self.assertIn("DZ-30型", pages[0])
        self.assertIn('name="supplier"', pages[1])

    def test_customer_create_edit_search_and_length_validation_keep_business_contact_separate(self):
        create_response = self.client.post(
            "/admin/customers",
            data={
                "name": "收货客户",
                "contact": "业务联系人",
                "phone": "业务电话",
                "recipient_name": "  仓库收货人  ",
                "recipient_phone": "  138 0000 0000  ",
                "address": "地址",
                "email": "",
                "remark": "",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT * FROM customers WHERE name = '收货客户'"
            ).fetchone()
        self.assertEqual(
            (
                customer["contact"],
                customer["phone"],
                customer["recipient_name"],
                customer["recipient_phone"],
            ),
            ("业务联系人", "业务电话", "仓库收货人", "138 0000 0000"),
        )

        edit_response = self.client.post(
            f"/admin/customers/{customer['id']}/edit",
            data={
                "name": "收货客户",
                "contact": "新业务联系人",
                "phone": "新业务电话",
                "recipient_name": " 新收货人 ",
                "recipient_phone": " 0574-12345678 ",
                "address": "地址",
                "email": "",
                "remark": "",
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer["id"],)
            ).fetchone()
        self.assertEqual(
            (
                customer["contact"],
                customer["phone"],
                customer["recipient_name"],
                customer["recipient_phone"],
            ),
            ("新业务联系人", "新业务电话", "新收货人", "0574-12345678"),
        )
        search_html = self.client.get(
            "/admin/customers", query_string={"q": "0574-12345678"}
        ).get_data(as_text=True)
        self.assertIn("收货客户", search_html)
        self.assertIn("新收货人", search_html)

        invalid_response = self.client.post(
            f"/admin/customers/{customer['id']}/edit",
            data={
                "name": "收货客户",
                "contact": "不得覆盖",
                "phone": "不得覆盖",
                "recipient_name": "收" * 101,
                "recipient_phone": "1" * 101,
                "address": "地址",
                "email": "",
                "remark": "",
            },
            follow_redirects=True,
        )
        self.assertIn("收货人不能超过 100 个字符", invalid_response.get_data(as_text=True))
        with app.get_db() as conn:
            unchanged = conn.execute(
                "SELECT contact, phone, recipient_name, recipient_phone FROM customers WHERE id = ?",
                (customer["id"],),
            ).fetchone()
        self.assertEqual(tuple(unchanged), ("新业务联系人", "新业务电话", "新收货人", "0574-12345678"))

        invalid_phone = self.client.post(
            f"/admin/customers/{customer['id']}/edit",
            data={
                "name": "收货客户",
                "contact": "不得覆盖",
                "phone": "不得覆盖",
                "recipient_name": "合法收货人",
                "recipient_phone": "1" * 101,
                "address": "地址",
                "email": "",
                "remark": "",
            },
            follow_redirects=True,
        )
        self.assertIn("收货人联系方式不能超过 100 个字符", invalid_phone.get_data(as_text=True))
        with app.get_db() as conn:
            unchanged = conn.execute(
                "SELECT contact, phone, recipient_name, recipient_phone FROM customers WHERE id = ?",
                (customer["id"],),
            ).fetchone()
        self.assertEqual(tuple(unchanged), ("新业务联系人", "新业务电话", "新收货人", "0574-12345678"))

    def test_ordinary_shipments_use_saved_specification_and_legacy_fallback(self):
        product = self.create_product("P-SPEC", "客户A")
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier = '规格-A' WHERE id = ?", (product,))
        order = self.create_order(product, "SO-SPEC", 3)
        self.stock_product(product, 3)

        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-09-08",
                "logistics_no": "",
                "order_id": str(order),
                "shipped_quantity": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            saved = conn.execute(
                "SELECT id, specification_snapshot FROM product_order_shipments WHERE order_id = ?",
                (order,),
            ).fetchone()
            self.assertEqual(saved["specification_snapshot"], "规格-A")
            conn.execute("UPDATE manuals SET supplier = '规格-B' WHERE id = ?", (product,))
            legacy_id = conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at
                ) VALUES (?, 1, '2026-09-09', '2026-09-09T10:00:00')
                """,
                (order,),
            ).lastrowid
            rows = app.fetch_shipped_orders(conn)

        specifications = {
            row["id"]: (row["specification"], row["specification_is_fallback"])
            for row in rows
        }
        self.assertEqual(specifications[saved["id"]], ("规格-A", 0))
        self.assertEqual(specifications[legacy_id], ("规格-B", 1))

    def test_new_empty_specification_snapshot_does_not_fall_back_after_product_change(self):
        product = self.create_product("P-EMPTY-SPEC", "客户A")
        order = self.create_order(product, "SO-EMPTY-SPEC", 1)
        self.stock_product(product, 1)
        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-09-08",
                "order_id": str(order),
                "shipped_quantity": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment_id = conn.execute(
                "SELECT id FROM product_order_shipments WHERE order_id = ?", (order,)
            ).fetchone()["id"]
            conn.execute("UPDATE manuals SET supplier = '后来填写' WHERE id = ?", (product,))
            shipment = app.fetch_shipment_by_id(conn, shipment_id)
        self.assertEqual(shipment["specification"], "")
        self.assertEqual(shipment["specification_is_fallback"], 0)

    def test_assembly_create_and_edit_preserve_specification_snapshot(self):
        product = self.create_product("P-ASM-SPEC", "客户A")
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier = '组装规格-A' WHERE id = ?", (product,))
        self.configure_component(product, quantity_per_set=1)
        self.create_order(product, "SO-ASM-SPEC", 2)
        self.stock_product(product, 2)
        preview = self.post_preview(sets=2).get_json()
        create_response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=self.assembly_save_data(preview),
        )
        self.assertEqual(create_response.status_code, 201, create_response.get_data(as_text=True))
        batch_id = create_response.get_json()["batch_id"]

        with app.get_db() as conn:
            item = conn.execute(
                "SELECT specification_snapshot FROM assembly_shipment_items WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            self.assertEqual(item["specification_snapshot"], "组装规格-A")
            conn.execute("UPDATE manuals SET supplier = '组装规格-B' WHERE id = ?", (product,))
            batch = app._fetch_assembly_shipment_batches_by_ids(conn, [batch_id])[0]
        self.assertEqual(batch["items"][0]["specification"], "组装规格-A")

        edit_preview = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            json={"overrides": {str(product): "2"}},
        ).get_json()
        edit_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            data=self.assembly_save_data(edit_preview, shipped_at="2026-09-09"),
        )
        self.assertEqual(edit_response.status_code, 201, edit_response.get_data(as_text=True))
        with app.get_db() as conn:
            edited = app._fetch_assembly_shipment_batches_by_ids(conn, [batch_id])[0]
        self.assertEqual(edited["items"][0]["specification"], "组装规格-A")

    def test_shipped_list_and_excel_project_specification(self):
        product = self.create_product("P-EXPORT-SPEC", "客户A")
        order = self.create_order(product, "SO-EXPORT-SPEC", 1)
        shipment_id = self.create_shipment(order, 1)
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier = '导出规格' WHERE id = ?", (product,))
            shipped_orders = app.fetch_shipped_orders_by_ids(conn, [shipment_id])

        html = self.client.get("/admin/shipped-orders").get_data(as_text=True)
        self.assertIn("规格型号", html)
        self.assertIn("导出规格", html)
        workbook = app.build_shipped_orders_workbook(shipped_orders, "", "", "")
        worksheet = workbook.active
        self.assertEqual(worksheet.cell(row=3, column=6).value, "规格型号")
        self.assertEqual(
            worksheet.cell(row=4, column=6).value,
            "导出规格（当前规格，历史未保存）",
        )

    def test_legacy_specification_fallback_is_labeled_in_history_result_pdf_and_excel(self):
        product = self.create_product("P-LEGACY-LABEL", "客户A")
        order = self.create_order(product, "SO-LEGACY-LABEL", 2)
        self.stock_product(product, 2)
        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "operation_token": "legacy-label",
                "shipped_at": "2026-09-08",
                "order_id": str(order),
                "shipped_quantity": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment_id = conn.execute(
                "SELECT id FROM product_order_shipments WHERE order_id=?", (order,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE product_order_shipments SET specification_snapshot=NULL WHERE id=?",
                (shipment_id,),
            )
            conn.execute(
                "UPDATE manuals SET supplier='当前产品规格' WHERE id=?", (product,)
            )
            shipment = app.fetch_shipment_by_id(conn, shipment_id)
            note_id = conn.execute(
                "SELECT note_id FROM delivery_note_sources WHERE source_type='ordinary' AND source_id=?",
                (shipment_id,),
            ).fetchone()[0]
            # Model an actual historical note: new notes now freeze lines.
            conn.execute('DELETE FROM delivery_note_items WHERE note_id=?', (note_id,))
            note = app.load_delivery_note(conn, note_id)

        label = "当前规格，历史未保存"
        self.assertEqual(shipment["specification"], "当前产品规格")
        self.assertEqual(shipment["specification_is_fallback"], 1)
        self.assertEqual(note["items"][0]["specification_is_fallback"], 1)
        self.assertIn(label, self.client.get('/admin/shipped-orders').get_data(as_text=True))
        self.assertIn(label, self.client.get(response.location).get_data(as_text=True))

        workbook = app.build_shipped_orders_workbook([shipment], "", "", "")
        self.assertEqual(workbook.active.cell(row=4, column=6).value,
                         f"当前产品规格（{label}）")
        story = []
        with patch.object(app.SimpleDocTemplate, 'build', lambda _doc, values: story.extend(values)):
            app.build_shipped_orders_pdf(note['items'], '', note['customer'], '', recipient_metadata=note)
        self.assertIn(label, ShippedPdfCompanyTests.collect_story_text(story))

        with app.get_db() as conn:
            conn.execute(
                "UPDATE product_order_shipments SET specification_snapshot='' WHERE id=?",
                (shipment_id,),
            )
            saved_empty = app.fetch_shipment_by_id(conn, shipment_id)
        self.assertEqual(saved_empty["specification"], "")
        self.assertEqual(saved_empty["specification_is_fallback"], 0)
        empty_workbook = app.build_shipped_orders_workbook([saved_empty], "", "", "")
        self.assertNotIn(label, str(empty_workbook.active.cell(row=4, column=6).value or ''))

    def test_product_import_help_explains_dual_specification_mapping(self):
        html = self.client.get("/admin/products/import").get_data(as_text=True)
        self.assertIn("“规格型号”同时用于产品图号匹配并写入产品列表的规格型号", html)


if __name__ == "__main__":
    unittest.main()
