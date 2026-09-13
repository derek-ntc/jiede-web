import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def get_material_rows(conn, manual_id):
    return conn.execute(
        """
        SELECT *
        FROM product_materials
        WHERE manual_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (manual_id,),
    ).fetchall()


def add_test_inventory(conn, manual_id, quantity):
    now = "2026-07-03T16:50:00"
    location_id = conn.execute(
        """
        INSERT INTO warehouse_locations (name, code, remark, enabled, created_at, updated_at)
        VALUES ('测试库位', ?, '', 1, ?, ?)
        """,
        (f"TEST-{manual_id}", now, now),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO inventory_balances (manual_id, location_id, quantity, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (manual_id, location_id, quantity, now),
    )
    return location_id


class InspectionReportTests(unittest.TestCase):
    def test_product_materials_can_be_created_displayed_searched_and_updated(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            create_response = client.post(
                "/admin/upload",
                data={
                    "drawing_no": "MAT-001",
                    "product_name": "材料测试产品",
                    "supplier": "产品供应商",
                    "customer": "",
                    "pack_quantity": "",
                    "pack_carton_size": "",
                    "pack_weight": "",
                    "remark": "",
                    "description_html": "",
                    "material": ["304", "Q235", ""],
                    "material_thickness": ["2.0mm", "5mm", ""],
                    "surface_type": ["拉丝", "喷砂", ""],
                    "material_supplier": ["材料供应商A", "材料供应商B", ""],
                },
            )
            self.assertEqual(create_response.status_code, 302)

            with app.get_db() as conn:
                manual = conn.execute("SELECT id FROM manuals WHERE drawing_no = 'MAT-001'").fetchone()
                materials = get_material_rows(conn, manual["id"])

            self.assertEqual(len(materials), 2)
            self.assertEqual(materials[0]["material"], "304")
            self.assertEqual(materials[1]["supplier"], "材料供应商B")

            list_html = client.get("/admin/products").get_data(as_text=True)
            self.assertIn("材料信息", list_html)
            self.assertIn("304", list_html)
            self.assertIn("2.0mm", list_html)
            self.assertIn("拉丝", list_html)
            self.assertIn("材料供应商A", list_html)

            search_html = client.get("/admin/products?q=喷砂").get_data(as_text=True)
            self.assertIn("MAT-001", search_html)

            admin_html = client.get("/admin").get_data(as_text=True)
            self.assertIn('data-material-history-select', admin_html)
            self.assertIn('data-target-name="material"', admin_html)
            self.assertIn('data-target-name="material_thickness"', admin_html)
            self.assertIn('data-target-name="surface_type"', admin_html)
            self.assertIn('data-target-name="material_supplier"', admin_html)
            self.assertIn('<option value="304">304</option>', admin_html)
            self.assertIn('<option value="2.0mm">2.0mm</option>', admin_html)
            self.assertIn('<option value="拉丝">拉丝</option>', admin_html)
            self.assertIn('<option value="材料供应商A">材料供应商A</option>', admin_html)

            edit_html = client.get(
                f"/admin/{manual['id']}/edit/technical"
            ).get_data(as_text=True)
            self.assertIn('<option value="Q235">Q235</option>', edit_html)
            self.assertIn('<option value="5mm">5mm</option>', edit_html)
            self.assertIn('<option value="喷砂">喷砂</option>', edit_html)
            self.assertIn('<option value="材料供应商B">材料供应商B</option>', edit_html)

            edit_response = client.post(
                f"/admin/{manual['id']}/edit/technical",
                data={
                    "description_html": "",
                    "material": ["铝板"],
                    "material_thickness": ["1.5mm"],
                    "surface_type": ["氧化"],
                    "material_supplier": ["材料供应商C"],
                },
            )
            self.assertEqual(edit_response.status_code, 302)

            with app.get_db() as conn:
                materials = get_material_rows(conn, manual["id"])
            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0]["material"], "铝板")
            self.assertEqual(materials[0]["supplier"], "材料供应商C")

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_powder_page_is_disabled_and_unified_procurement_is_available(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            dashboard_html = client.get("/dashboard").get_data(as_text=True)
            admin_html = client.get("/admin").get_data(as_text=True)
            users_html = client.get("/admin/users").get_data(as_text=True)

            for html in (dashboard_html, admin_html, users_html):
                self.assertNotIn("喷塑记录", html)
                self.assertNotIn("/admin/powder-coating", html)
            self.assertIn("统一采购", dashboard_html)
            self.assertIn("/admin/purchases/raw-material", dashboard_html)
            self.assertIn("统一采购", admin_html)
            self.assertIn("/admin/purchases/raw-material", admin_html)
            self.assertIn("纸箱/到货记录", users_html)
            self.assertIn("到货记录", dashboard_html)
            self.assertIn("到货记录", admin_html)

            self.assertEqual(client.get("/admin/powder-coating").status_code, 404)
            response = client.get("/admin/carton-purchases")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/admin/purchases/carton")

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_orders_cannot_create_legacy_carton_purchase_records_after_migration(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-06T15:30:00"
                manual_id = conn.execute(
                    """
                    INSERT INTO manuals (
                        product_name, customer, model, category, version, remark,
                        filename, original_filename, created_at, updated_at,
                        drawing_no, supplier, pack_quantity, pack_carton_size
                    )
                    VALUES ('纸箱测试产品', '客户A', '', '', '', '', '', '', ?, ?,
                            'JDX-001', '产品供应商', '40PCS', '31x32x33CM')
                    """,
                    (now, now),
                ).lastrowid
                order_id = conn.execute(
                    """
                    INSERT INTO product_orders (
                        manual_id, order_no, ordered_at, quantity, customer,
                        planned_ship_at, created_at, updated_at
                    )
                    VALUES (?, 'SO-CARTON', '2026-07-06', 100, '客户A', '2026-07-10', ?, ?)
                    """,
                    (manual_id, now, now),
                ).lastrowid
                conn.execute(
                    """
                    UPDATE manuals
                    SET pack_carton_size = '310x320x330'
                    WHERE id = ?
                    """,
                    (manual_id,),
                )

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            orders_html = client.get(f"/admin/orders/groups/{order_id}").get_data(as_text=True)
            self.assertNotIn("生成纸箱采购清单", orders_html)
            self.assertIn("/admin/purchases/carton", orders_html)

            response = client.post(
                "/admin/orders/carton-purchases",
                data={"order_id": [str(order_id)]},
            )
            self.assertEqual(response.status_code, 409)

            with app.get_db() as conn:
                rows = conn.execute("SELECT * FROM carton_purchases").fetchall()
            self.assertEqual(len(rows), 0)

            preview_response = client.post(
                "/admin/carton-purchases/purchase-order",
                data={"record_id": ["1"]},
            )
            self.assertEqual(preview_response.status_code, 409)

            pdf_response = client.post(
                "/admin/carton-purchases/purchase-order",
                data={"record_id": ["1"], "download": "1"},
            )
            self.assertEqual(pdf_response.status_code, 409)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_completed_shipment_plan_can_be_deleted_without_deleting_shipments(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T17:10:00"
                manual_id = conn.execute(
                    """
                    INSERT INTO manuals (
                        product_name, customer, model, category, version, remark,
                        filename, original_filename, created_at, updated_at, drawing_no
                    )
                    VALUES ('测试产品', '客户A', '', '', '', '', '', '', ?, ?, 'D-001')
                    """,
                    (now, now),
                ).lastrowid
                order_id = conn.execute(
                    """
                    INSERT INTO product_orders (
                        manual_id, order_no, ordered_at, quantity, customer,
                        planned_ship_at, created_at, updated_at
                    )
                    VALUES (?, 'SO-DEL', '2026-07-03', 10, '客户A', '2026-07-04', ?, ?)
                    """,
                    (manual_id, now, now),
                ).lastrowid
                plan_id = conn.execute(
                    """
                    INSERT INTO shipment_plans (
                        plan_no, planned_ship_at, customer, status, remark,
                        created_by, created_at, updated_at
                    )
                    VALUES ('SP-DEL', '2026-07-04', '客户A', '已完成', '', 'admin', ?, ?)
                    """,
                    (now, now),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO shipment_plan_items (
                        plan_id, order_id, planned_quantity, shipped_quantity,
                        created_at, updated_at
                    )
                    VALUES (?, ?, 10, 10, ?, ?)
                    """,
                    (plan_id, order_id, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO product_order_shipments (
                        order_id, shipped_quantity, shipped_at, created_at,
                        signature_token, signature_status, signature_expires_at
                    )
                    VALUES (?, 10, '2026-07-03', ?, 'token-del', '未签收', '2026-07-10T17:10:00')
                    """,
                    (order_id, now),
                )

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            page = client.get("/admin/shipped-orders").get_data(as_text=True)
            self.assertNotIn("SP-DEL", page)

            response = client.post(f"/admin/shipped-orders/plans/{plan_id}/delete")

            self.assertEqual(response.status_code, 302)
            with app.get_db() as conn:
                plan_count = conn.execute("SELECT COUNT(*) AS c FROM shipment_plans").fetchone()["c"]
                shipment_count = conn.execute("SELECT COUNT(*) AS c FROM product_order_shipments").fetchone()["c"]
            self.assertEqual(plan_count, 0)
            self.assertEqual(shipment_count, 1)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_pending_shipment_plan_can_be_approved(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T17:15:00"
                manual_id = conn.execute(
                    """
                    INSERT INTO manuals (
                        product_name, customer, model, category, version, remark,
                        filename, original_filename, created_at, updated_at, drawing_no
                    )
                    VALUES ('测试产品', '客户A', '', '', '', '', '', '', ?, ?, 'D-001')
                    """,
                    (now, now),
                ).lastrowid
                order_id = conn.execute(
                    """
                    INSERT INTO product_orders (
                        manual_id, order_no, ordered_at, quantity, customer,
                        planned_ship_at, created_at, updated_at
                    )
                    VALUES (?, 'SO-APP', '2026-07-03', 5, '客户A', '2026-07-04', ?, ?)
                    """,
                    (manual_id, now, now),
                ).lastrowid
                plan_id = conn.execute(
                    """
                    INSERT INTO shipment_plans (
                        plan_no, planned_ship_at, customer, status, remark,
                        created_by, created_at, updated_at
                    )
                    VALUES ('SP-APP', '2026-07-04', '客户A', '待发货', '', 'admin', ?, ?)
                    """,
                    (now, now),
                ).lastrowid
                item_id = conn.execute(
                    """
                    INSERT INTO shipment_plan_items (
                        plan_id, order_id, planned_quantity, shipped_quantity,
                        created_at, updated_at
                    )
                    VALUES (?, ?, 4, 0, ?, ?)
                    """,
                    (plan_id, order_id, now, now),
                ).lastrowid
                add_test_inventory(conn, manual_id, 5)

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            response = client.post(
                f"/admin/shipped-orders/plans/{plan_id}/approve",
                data={
                    "plan_item_id": [str(item_id)],
                    "shipped_quantity": ["4"],
                    "shipped_at": "2026-07-03",
                    "logistics_no": "",
                },
            )

            self.assertEqual(response.status_code, 302)
            with app.get_db() as conn:
                plan = conn.execute("SELECT status FROM shipment_plans WHERE id = ?", (plan_id,)).fetchone()
                shipment_count = conn.execute("SELECT COUNT(*) AS c FROM product_order_shipments").fetchone()["c"]
            self.assertEqual(plan["status"], "已完成")
            self.assertEqual(shipment_count, 1)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_creating_shipment_does_not_send_delivery_email(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        original_sender = app.send_delivery_note_for_shipment_ids
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T16:55:00"
                manual_id = conn.execute(
                    """
                    INSERT INTO manuals (
                        product_name, customer, model, category, version, remark,
                        filename, original_filename, created_at, updated_at, drawing_no
                    )
                    VALUES ('测试产品', '客户A', '', '', '', '', '', '', ?, ?, 'D-001')
                    """,
                    (now, now),
                ).lastrowid
                order_id = conn.execute(
                    """
                    INSERT INTO product_orders (
                        manual_id, order_no, ordered_at, quantity, customer,
                        planned_ship_at, created_at, updated_at, customer_email
                    )
                    VALUES (?, 'SO-001', '2026-07-03', 5, '客户A', '2026-07-04', ?, ?, 'client@example.com')
                    """,
                    (manual_id, now, now),
                ).lastrowid
                add_test_inventory(conn, manual_id, 5)

            def fail_if_called(conn, shipment_ids):
                raise AssertionError("delivery email sender should not be called")

            app.send_delivery_note_for_shipment_ids = fail_if_called
            try:
                client = app.app.test_client()
                with client.session_transaction() as session:
                    session["admin_logged_in"] = True
                    session["admin_username"] = "admin"
                    session["admin_role"] = "admin"

                response = client.post(
                    "/admin/shipped-orders/new",
                    data={
                        "order_id": [str(order_id)],
                        "shipped_quantity": ["2"],
                        "shipped_at": "2026-07-03",
                        "logistics_no": "",
                    },
                )

                self.assertEqual(response.status_code, 302)
                with app.get_db() as conn:
                    shipment_count = conn.execute(
                        "SELECT COUNT(*) AS c FROM product_order_shipments"
                    ).fetchone()["c"]
                self.assertEqual(shipment_count, 1)
            finally:
                app.send_delivery_note_for_shipment_ids = original_sender

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_shipped_orders_show_customer_signature_link_directly(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T16:55:00"
                manual_id = conn.execute(
                    """
                    INSERT INTO manuals (
                        product_name, customer, model, category, version, remark,
                        filename, original_filename, created_at, updated_at, drawing_no
                    )
                    VALUES ('测试产品', '客户A', '', '', '', '', '', '', ?, ?, 'D-001')
                    """,
                    (now, now),
                ).lastrowid
                order_id = conn.execute(
                    """
                    INSERT INTO product_orders (
                        manual_id, order_no, ordered_at, quantity, customer,
                        planned_ship_at, created_at, updated_at
                    )
                    VALUES (?, 'SO-001', '2026-07-03', 5, '客户A', '2026-07-04', ?, ?)
                    """,
                    (manual_id, now, now),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO product_order_shipments (
                        order_id, shipped_quantity, shipped_at, created_at,
                        signature_token, signature_status, signature_expires_at
                    )
                    VALUES (?, 2, '2026-07-03', ?, 'token-123', '未签收', '2026-07-10T16:55:00')
                    """,
                    (order_id, now),
                )

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "admin"
                session["admin_role"] = "admin"

            response = client.get("/admin/shipped-orders")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("客户签字链接", html)
            self.assertIn("/sign/token-123", html)
            self.assertNotIn("<th>供应商</th>", html)
            self.assertNotIn("<th>物流单号</th>", html)
            self.assertIn("<th>操作</th>", html)
            self.assertIn(f'href="/admin/shipped-orders/1/edit">修改</a>', html)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_production_followup_page_shows_process_cards_and_selected_files_ui(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T15:00:00"
                conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, active,
                        can_manage_production_followups, created_at, updated_at
                    )
                    VALUES ('viewer', 'unused', 'operator', 1, 1, ?, ?)
                    """,
                    (now, now),
                )
                conn.execute(
                    """
                    INSERT INTO production_followups (
                        batch_no, ordered_at, drawing_no, product_name, created_at, updated_at
                    )
                    VALUES ('PF-20260703-001', '2026-07-03', 'D-001', '测试产品', ?, ?)
                    """,
                    (now, now),
                )
                conn.execute(
                    """
                    INSERT INTO production_followups (
                        batch_no, ordered_at, drawing_no, product_name,
                        laser_completed_at, bending_completed_at, welding_completed_at,
                        created_at, updated_at
                    )
                    VALUES (
                        'PF-20260703-002', '2026-07-03', 'D-002', '完成产品',
                        '2026-07-03T15:10:00', '2026-07-03T15:20:00', '2026-07-03T15:30:00',
                        ?, ?
                    )
                    """,
                    (now, now),
                )

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "viewer"
                session["admin_role"] = "operator"

            response = client.get("/admin/production-followups?view=legacy")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn('class="production-followup-inputs"', html)
            self.assertIn("<th>生产工艺</th>", html)
            self.assertIn('name="process_name" value="激光"', html)
            self.assertIn('name="process_name" value="折弯"', html)
            self.assertIn('name="process_name" value="焊接"', html)
            self.assertIn("<th>操作</th>", html)
            self.assertIn("order-completed", html)
            self.assertIn("/admin/production-followups/2/delete", html)
            self.assertNotIn("<th>激光</th>", html)
            self.assertIn("data-production-drawing-input", html)
            self.assertIn("data-selected-production-drawings", html)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_production_process_card_preview_renders_two_cards(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T13:50:00"
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role, active, created_at, updated_at)
                    VALUES ('viewer', 'unused', 'operator', 1, ?, ?)
                    """,
                    (now, now),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO production_followups (
                        batch_no, ordered_at, drawing_no, product_name,
                        laser_completed_at, created_at, updated_at
                    )
                    VALUES (
                        'PF-20260703-888', '2026-07-03', 'D-888', '测试工艺卡',
                        '2026-07-03T14:10:00', ?, ?
                    )
                    """,
                    (now, now),
                )
                followup_id = cursor.lastrowid

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "viewer"
                session["admin_role"] = "operator"

            response = client.get(f"/admin/production-followups/{followup_id}/process-card")
            html = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn("PF-20260703-888", html)
            self.assertIn("测试工艺卡", html)
            self.assertNotIn("未完成", html)
            self.assertEqual(html.count('process-stage-check is-checked'), 2)
            self.assertEqual(html.count('class="process-card"'), 2)

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_production_stage_button_toggles_complete_and_revert(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T13:40:00"
                conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, active,
                        can_manage_production_followups, created_at, updated_at
                    )
                    VALUES ('worker', 'unused', 'operator', 1, 1, ?, ?)
                    """,
                    (now, now),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO production_followups (
                        batch_no, ordered_at, drawing_no, product_name, created_at, updated_at
                    )
                    VALUES ('PF-20260703-999', '2026-07-03', 'D-001', '测试产品', ?, ?)
                    """,
                    (now, now),
                )
                followup_id = cursor.lastrowid

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "worker"
                session["admin_role"] = "operator"
                session["production_followup_csrf_token"] = "stage-csrf"

            client.post(
                f"/admin/production-followups/{followup_id}/laser",
                data={"production_followup_csrf_token": "stage-csrf"},
            )
            with app.get_db() as conn:
                row = conn.execute(
                    "SELECT laser_completed_at FROM production_followups WHERE id = ?",
                    (followup_id,),
                ).fetchone()
                self.assertTrue(row["laser_completed_at"])

            client.post(
                f"/admin/production-followups/{followup_id}/laser",
                data={"production_followup_csrf_token": "stage-csrf"},
            )
            with app.get_db() as conn:
                row = conn.execute(
                    "SELECT laser_completed_at FROM production_followups WHERE id = ?",
                    (followup_id,),
                ).fetchone()
                self.assertEqual(row["laser_completed_at"], "")

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready

    def test_delete_production_followup_removes_row_and_files(self):
        original_db_path = app.DB_PATH
        original_database_ready = app.DATABASE_READY
        original_drawings_dir = app.PRODUCTION_DRAWINGS_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "manuals.db"
            app.PRODUCTION_DRAWINGS_DIR = Path(tmpdir) / "production-drawings"
            app.PRODUCTION_DRAWINGS_DIR.mkdir(parents=True, exist_ok=True)
            app.DATABASE_READY = False
            app.init_db()
            with app.get_db() as conn:
                now = "2026-07-03T13:35:00"
                conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, active,
                        can_manage_production_followups, created_at, updated_at
                    )
                    VALUES ('worker', 'unused', 'operator', 1, 1, ?, ?)
                    """,
                    (now, now),
                )
                followup_id = conn.execute(
                    """
                    INSERT INTO production_followups (
                        batch_no, ordered_at, drawing_no, product_name, created_at, updated_at
                    )
                    VALUES ('PF-20260703-777', '2026-07-03', 'D-777', '待删除产品', ?, ?)
                    """,
                    (now, now),
                ).lastrowid
                drawing_path = app.PRODUCTION_DRAWINGS_DIR / "delete-me.pdf"
                drawing_path.write_text("drawing")
                conn.execute(
                    """
                    INSERT INTO production_followup_files (
                        followup_id, filename, original_filename, created_at
                    )
                    VALUES (?, 'delete-me.pdf', '图纸.pdf', ?)
                    """,
                    (followup_id, now),
                )

            client = app.app.test_client()
            with client.session_transaction() as session:
                session["admin_logged_in"] = True
                session["admin_username"] = "worker"
                session["admin_role"] = "operator"

            with client.session_transaction() as session:
                session['production_followup_csrf_token'] = 'test-delete-token'
            response = client.post(f"/admin/production-followups/{followup_id}/delete",
                data={'production_followup_csrf_token': 'test-delete-token'})

            self.assertEqual(response.status_code, 302)
            with app.get_db() as conn:
                followup_count = conn.execute("SELECT COUNT(*) AS c FROM production_followups").fetchone()["c"]
                file_count = conn.execute("SELECT COUNT(*) AS c FROM production_followup_files").fetchone()["c"]
            self.assertEqual(followup_count, 0)
            self.assertEqual(file_count, 0)
            self.assertFalse(drawing_path.exists())

        app.DB_PATH = original_db_path
        app.DATABASE_READY = original_database_ready
        app.PRODUCTION_DRAWINGS_DIR = original_drawings_dir

    def test_user_table_has_production_followup_action_permission(self):
        conn = memory_db()
        app.ensure_user_table(conn)

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }

        self.assertIn("can_manage_production_followups", columns)

    def test_production_followup_stage_revert_sequence(self):
        conn = memory_db()
        app.ensure_production_followup_tables(conn)
        conn.execute(
            """
            INSERT INTO production_followups (
                ordered_at, drawing_no, product_name,
                laser_completed_at, bending_completed_at, welding_completed_at,
                created_at, updated_at
            )
            VALUES (
                '2026-07-03', 'D-001', '测试产品',
                '2026-07-03T10:00:00', '2026-07-03T11:00:00', '2026-07-03T12:00:00',
                '2026-07-03', '2026-07-03'
            )
            """
        )
        row = conn.execute("SELECT * FROM production_followups").fetchone()

        self.assertFalse(app.production_stage_can_revert(row, "laser"))
        self.assertFalse(app.production_stage_can_revert(row, "bending"))
        self.assertTrue(app.production_stage_can_revert(row, "welding"))

        conn.execute("UPDATE production_followups SET welding_completed_at = ''")
        row = conn.execute("SELECT * FROM production_followups").fetchone()
        self.assertTrue(app.production_stage_can_revert(row, "bending"))
        self.assertFalse(app.production_stage_can_revert(row, "laser"))

    def test_fetch_production_followups_filters_batch_product_and_file_name(self):
        conn = memory_db()
        app.ensure_production_followup_tables(conn)
        conn.executescript(
            """
            INSERT INTO production_followups (
                id, batch_no, ordered_at, drawing_no, product_name, created_at, updated_at
            )
            VALUES
                (1, 'PF-20260703-001', '2026-07-03', 'D-001', '架子', '2026-07-03', '2026-07-03'),
                (2, 'PF-20260704-001', '2026-07-04', 'D-002', '外壳', '2026-07-04', '2026-07-04');
            INSERT INTO production_followup_files (
                followup_id, filename, original_filename, created_at
            )
            VALUES (2, '2-test-file.pdf', 'laser-drawing.pdf', '2026-07-04');
            """
        )

        by_batch = app.fetch_production_followups(conn, "20260703")
        by_product = app.fetch_production_followups(conn, "外壳")
        by_file = app.fetch_production_followups(conn, "laser")

        self.assertEqual([item["row"]["id"] for item in by_batch], [1])
        self.assertEqual([item["row"]["id"] for item in by_product], [2])
        self.assertEqual([item["row"]["id"] for item in by_file], [2])

    def test_next_production_batch_no_increments_by_order_date(self):
        conn = memory_db()
        app.ensure_production_followup_tables(conn)

        first = app.next_production_batch_no(conn, "2026-07-03")
        conn.execute(
            """
            INSERT INTO production_followups (
                batch_no, ordered_at, drawing_no, product_name, created_at, updated_at
            )
            VALUES (?, '2026-07-03', 'D-001', '测试产品', '2026-07-03', '2026-07-03')
            """,
            (first,),
        )

        second = app.next_production_batch_no(conn, "2026-07-03")

        self.assertEqual(first, "PF-20260703-001")
        self.assertEqual(second, "PF-20260703-002")

    def test_production_followup_stage_sequence(self):
        conn = memory_db()
        app.ensure_production_followup_tables(conn)
        conn.execute(
            """
            INSERT INTO production_followups (
                ordered_at, drawing_no, product_name, created_at, updated_at
            )
            VALUES ('2026-07-03', 'D-001', '测试产品', '2026-07-03', '2026-07-03')
            """
        )
        row = conn.execute("SELECT * FROM production_followups").fetchone()

        self.assertTrue(app.production_stage_can_complete(row, "laser"))
        self.assertFalse(app.production_stage_can_complete(row, "bending"))
        self.assertFalse(app.production_stage_can_complete(row, "welding"))

        conn.execute("UPDATE production_followups SET laser_completed_at = '2026-07-03T10:00:00'")
        row = conn.execute("SELECT * FROM production_followups").fetchone()
        self.assertTrue(app.production_stage_can_complete(row, "bending"))
        self.assertFalse(app.production_stage_can_complete(row, "welding"))

        conn.execute("UPDATE production_followups SET bending_completed_at = '2026-07-03T11:00:00'")
        row = conn.execute("SELECT * FROM production_followups").fetchone()
        self.assertTrue(app.production_stage_can_complete(row, "welding"))

    def test_save_manual_inspection_requirements_replaces_blank_rows(self):
        conn = memory_db()
        app.ensure_inspection_tables(conn)

        app.save_manual_inspection_requirements(
            conn,
            7,
            [
                {
                    "item_name": "外观",
                    "standard": "无划伤、变形、锈蚀",
                    "method": "目视",
                    "remark": "",
                },
                {
                    "item_name": "",
                    "standard": "",
                    "method": "",
                    "remark": "",
                },
            ],
        )

        rows = app.get_manual_inspection_requirements(conn, 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_name"], "外观")
        self.assertEqual(rows[0]["standard"], "无划伤、变形、锈蚀")
        self.assertEqual(rows[0]["method"], "目视")

    def test_build_shipment_plan_inspection_sections_groups_requirements_by_product(self):
        conn = memory_db()
        conn.executescript(
            """
            CREATE TABLE manuals (
                id INTEGER PRIMARY KEY,
                drawing_no TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL,
                customer TEXT NOT NULL DEFAULT '',
                pack_quantity TEXT NOT NULL DEFAULT '',
                pack_carton_size TEXT NOT NULL DEFAULT '',
                pack_weight TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE product_orders (
                id INTEGER PRIMARY KEY,
                manual_id INTEGER NOT NULL,
                order_no TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                shipped_quantity INTEGER NOT NULL DEFAULT 0,
                customer TEXT NOT NULL DEFAULT '',
                planned_ship_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE shipment_plans (
                id INTEGER PRIMARY KEY,
                plan_no TEXT NOT NULL,
                planned_ship_at TEXT NOT NULL DEFAULT '',
                customer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '待发货',
                remark TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE shipment_plan_items (
                id INTEGER PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                planned_quantity INTEGER NOT NULL DEFAULT 0,
                shipped_quantity INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE product_order_shipments (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                shipped_quantity INTEGER NOT NULL,
                shipped_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        app.ensure_inspection_tables(conn)
        conn.execute(
            "INSERT INTO manuals (id, drawing_no, product_name) VALUES (1, 'D-001', '测试产品')"
        )
        conn.execute(
            "INSERT INTO product_orders (id, manual_id, order_no, quantity) VALUES (2, 1, 'PO-001', 100)"
        )
        conn.execute(
            """
            INSERT INTO shipment_plans (
                id, plan_no, planned_ship_at, customer, created_at, updated_at
            )
            VALUES (3, 'SP-001', '2026-07-03', '客户A', '2026-07-03', '2026-07-03')
            """
        )
        conn.execute(
            """
            INSERT INTO shipment_plan_items (
                id, plan_id, order_id, planned_quantity, shipped_quantity, created_at, updated_at
            )
            VALUES (4, 3, 2, 80, 0, '2026-07-03', '2026-07-03')
            """
        )
        app.save_manual_inspection_requirements(
            conn,
            1,
            [
                {"item_name": "尺寸", "standard": "按图纸", "method": "卡尺", "remark": "关键尺寸"},
                {"item_name": "包装", "standard": "标签正确", "method": "核对", "remark": ""},
            ],
        )

        plan = app.fetch_shipment_plan_detail(conn, 3)
        sections = app.build_shipment_plan_inspection_sections(conn, plan)

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["drawing_no"], "D-001")
        self.assertEqual(sections[0]["planned_quantity"], 80)
        self.assertEqual(
            [row["item_name"] for row in sections[0]["requirements"]],
            ["尺寸", "包装"],
        )

    def test_fetch_open_shipment_plans_creates_missing_inspection_report_table(self):
        conn = memory_db()
        conn.executescript(
            """
            CREATE TABLE manuals (
                id INTEGER PRIMARY KEY,
                drawing_no TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL,
                customer TEXT NOT NULL DEFAULT '',
                pack_quantity TEXT NOT NULL DEFAULT '',
                pack_carton_size TEXT NOT NULL DEFAULT '',
                pack_weight TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE product_orders (
                id INTEGER PRIMARY KEY,
                manual_id INTEGER NOT NULL,
                order_no TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                shipped_quantity INTEGER NOT NULL DEFAULT 0,
                customer TEXT NOT NULL DEFAULT '',
                planned_ship_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE shipment_plans (
                id INTEGER PRIMARY KEY,
                plan_no TEXT NOT NULL,
                planned_ship_at TEXT NOT NULL DEFAULT '',
                customer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '待发货',
                remark TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE shipment_plan_items (
                id INTEGER PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                planned_quantity INTEGER NOT NULL DEFAULT 0,
                shipped_quantity INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE product_order_shipments (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                shipped_quantity INTEGER NOT NULL,
                shipped_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO manuals (id, drawing_no, product_name) VALUES (1, 'D-001', '测试产品');
            INSERT INTO product_orders (id, manual_id, order_no, quantity) VALUES (2, 1, 'PO-001', 100);
            INSERT INTO shipment_plans (
                id, plan_no, planned_ship_at, customer, created_at, updated_at
            )
            VALUES (3, 'SP-001', '2026-07-03', '客户A', '2026-07-03', '2026-07-03');
            INSERT INTO shipment_plan_items (
                id, plan_id, order_id, planned_quantity, shipped_quantity, created_at, updated_at
            )
            VALUES (4, 3, 2, 80, 0, '2026-07-03', '2026-07-03');
            """
        )

        plans = app.fetch_open_shipment_plans(conn)

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["inspection_reports"], [])


if __name__ == "__main__":
    unittest.main()
