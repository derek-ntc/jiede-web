import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def create_product_and_order(self):
        with app.get_db() as conn:
            now = "2026-07-06T18:00:00"
            manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    product_name, customer, model, category, version, remark,
                    filename, original_filename, created_at, updated_at,
                    drawing_no, supplier, pack_quantity, pack_carton_size,
                    sku, barcode, min_stock
                )
                VALUES ('库存测试产品', '客户A', '', '', '', '规格：100x200',
                        '', '', ?, ?, 'INV-D001', '供应商A', '20PCS', '100x200x50',
                        '', 'BAR-001', 5)
                """,
                (now, now),
            ).lastrowid
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    planned_ship_at, created_at, updated_at
                )
                VALUES (?, 'SO-INV-001', '2026-07-06', 10, '客户A', '2026-07-12', ?, ?)
                """,
                (manual_id, now, now),
            ).lastrowid
        return manual_id, order_id

    def stock_product(self, manual_id, quantity=10):
        with app.get_db() as conn:
            now = "2026-07-06T18:05:00"
            location_id = conn.execute(
                """
                INSERT INTO warehouse_locations (name, code, remark, enabled, created_at, updated_at)
                VALUES ('发货测试库位', 'SHIP-01', '', 1, ?, ?)
                """,
                (now, now),
            ).lastrowid
            with app.app.test_request_context():
                app.create_inventory_transaction(
                    conn,
                    "in",
                    manual_id,
                    quantity,
                    to_location_id=location_id,
                    related_order_type="库存调整",
                    remark="发货测试备货",
                )
        return location_id

    def test_inventory_tables_codes_scan_in_out_and_order_status(self):
        manual_id, order_id = self.create_product_and_order()

        location_response = self.client.post(
            "/admin/inventory/locations",
            data={"name": "成品仓A区", "code": "A-01", "remark": "常用库位", "enabled": "1"},
        )
        self.assertEqual(location_response.status_code, 302)

        with app.get_db() as conn:
            location = conn.execute("SELECT * FROM warehouse_locations WHERE code = 'A-01'").fetchone()
        self.assertIsNotNone(location)

        overview_html = self.client.get("/admin/inventory").get_data(as_text=True)
        self.assertIn("库存总览", overview_html)
        self.assertIn("扫码查询", overview_html)
        self.assertIn("扫码入库", overview_html)
        self.assertIn("扫码出库", overview_html)
        self.assertIn("库存调整", overview_html)
        self.assertIn("库位管理", overview_html)
        self.assertIn("库存流水", overview_html)
        self.assertIn("P000001", overview_html)

        detail_html = self.client.get(f"/manual/{manual_id}").get_data(as_text=True)
        self.assertNotIn("库存二维码", detail_html)
        self.assertNotIn("data-qr-code=", detail_html)
        self.assertIn("打印标签", detail_html)
        self.assertIn("<h2>基本信息</h2>", detail_html)
        self.assertNotIn("<h2>附件</h2>", detail_html)

        technical_html = self.client.get(
            f"/manual/{manual_id}/technical"
        ).get_data(as_text=True)
        self.assertIn("技术资料", technical_html)
        self.assertIn("<h2>附件</h2>", technical_html)

        scan_response = self.client.get("/admin/inventory/api/product?code=P000001")
        self.assertEqual(scan_response.status_code, 200)
        self.assertEqual(scan_response.json["product"]["id"], manual_id)
        self.assertEqual(scan_response.json["product"]["inventory_code"], "P000001")

        inbound_response = self.client.post(
            "/admin/inventory/inbound",
            data={
                "manual_id": str(manual_id),
                "quantity": "6",
                "location_id": str(location["id"]),
                "stock_type": "生产入库",
                "related_order_no": "SO-INV-001",
                "related_order_id": str(order_id),
                "remark": "首批入库",
            },
        )
        self.assertEqual(inbound_response.status_code, 302)

        with app.get_db() as conn:
            balance = conn.execute(
                "SELECT quantity FROM inventory_balances WHERE manual_id = ? AND location_id = ?",
                (manual_id, location["id"]),
            ).fetchone()
            order = conn.execute("SELECT inventory_status, inventory_received_quantity FROM product_orders WHERE id = ?", (order_id,)).fetchone()
            transactions = conn.execute("SELECT * FROM inventory_transactions ORDER BY id").fetchall()

        self.assertEqual(balance["quantity"], 6)
        self.assertEqual(order["inventory_status"], "部分入库")
        self.assertEqual(order["inventory_received_quantity"], 6)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["type"], "in")
        self.assertEqual(transactions[0]["operator"], "admin")

        blocked_outbound = self.client.post(
            "/admin/inventory/outbound",
            data={
                "manual_id": str(manual_id),
                "quantity": "9",
                "location_id": str(location["id"]),
                "stock_type": "销售发货",
                "remark": "库存不足测试",
            },
        )
        self.assertEqual(blocked_outbound.status_code, 302)
        with app.get_db() as conn:
            quantity_after_block = conn.execute(
                "SELECT quantity FROM inventory_balances WHERE manual_id = ? AND location_id = ?",
                (manual_id, location["id"]),
            ).fetchone()["quantity"]
        self.assertEqual(quantity_after_block, 6)

        outbound_response = self.client.post(
            "/admin/inventory/outbound",
            data={
                "manual_id": str(manual_id),
                "quantity": "2",
                "location_id": str(location["id"]),
                "stock_type": "销售发货",
                "customer": "客户A",
                "related_order_no": "SO-INV-001",
                "remark": "发货出库",
            },
        )
        self.assertEqual(outbound_response.status_code, 302)

        with app.get_db() as conn:
            quantity_after_out = conn.execute(
                "SELECT quantity FROM inventory_balances WHERE manual_id = ? AND location_id = ?",
                (manual_id, location["id"]),
            ).fetchone()["quantity"]
            transaction_count = conn.execute("SELECT COUNT(*) AS c FROM inventory_transactions").fetchone()["c"]
        self.assertEqual(quantity_after_out, 4)
        self.assertEqual(transaction_count, 2)

        adjust_response = self.client.post(
            "/admin/inventory/adjust",
            data={
                "manual_id": str(manual_id),
                "location_id": str(location["id"]),
                "counted_quantity": "7",
                "remark": "盘点调整",
            },
        )
        self.assertEqual(adjust_response.status_code, 302)

        with app.get_db() as conn:
            adjusted_quantity = conn.execute(
                "SELECT quantity FROM inventory_balances WHERE manual_id = ? AND location_id = ?",
                (manual_id, location["id"]),
            ).fetchone()["quantity"]
            adjust_transaction = conn.execute(
                "SELECT * FROM inventory_transactions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(adjusted_quantity, 7)
        self.assertEqual(adjust_transaction["type"], "adjust")
        self.assertEqual(adjust_transaction["quantity"], 3)
        self.assertIn("盘点调整", adjust_transaction["remark"])

        orders_html = self.client.get("/admin/orders").get_data(as_text=True)
        self.assertIn("材料库存情况", orders_html)
        self.assertIn("库存数量", orders_html)
        self.assertIn(">7<", orders_html)

        low_stock_html = self.client.get("/admin/inventory?status=low").get_data(as_text=True)
        self.assertNotIn("库存测试产品", low_stock_html)

        transaction_html = self.client.get("/admin/inventory/transactions").get_data(as_text=True)
        self.assertIn("库存流水", transaction_html)
        self.assertIn("首批入库", transaction_html)
        self.assertIn("发货出库", transaction_html)
        self.assertIn("盘点调整", transaction_html)

        export_response = self.client.get("/admin/inventory/export.xlsx")
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("spreadsheet", export_response.mimetype)

    def test_sku_is_used_before_generated_inventory_code(self):
        with app.get_db() as conn:
            now = "2026-07-06T18:00:00"
            manual_id = conn.execute(
                """
                INSERT INTO manuals (
                    product_name, customer, model, category, version, remark,
                    filename, original_filename, created_at, updated_at,
                    drawing_no, sku
                )
                VALUES ('SKU测试产品', '', '', '', '', '', '', '', ?, ?, 'SKU-D001', 'SKU-ABC')
                """,
                (now, now),
            ).lastrowid

        html = self.client.get("/admin/inventory").get_data(as_text=True)
        self.assertIn("SKU-ABC", html)
        scan_response = self.client.get("/admin/inventory/api/product?code=SKU-ABC")
        self.assertEqual(scan_response.status_code, 200)
        self.assertEqual(scan_response.json["product"]["id"], manual_id)
        self.assertEqual(scan_response.json["product"]["inventory_code"], "SKU-ABC")

    def test_shipment_shortage_is_saved_with_limited_inventory_deduction(self):
        manual_id, order_id = self.create_product_and_order()
        location_id = self.stock_product(manual_id, 2)

        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-07-16",
                "order_id": [str(order_id)],
                "shipped_quantity": ["3"],
            },
            follow_redirects=True,
        )
        self.assertIn("库存不足", response.get_data(as_text=True))
        self.assertIn("实际扣减 2，缺货 1", response.get_data(as_text=True))
        with app.get_db() as conn:
            shipment_count = conn.execute("SELECT COUNT(*) AS c FROM product_order_shipments").fetchone()["c"]
            balance = app.inventory_balance_for_location(conn, manual_id, location_id)
            self.assertEqual(conn.execute("SELECT SUM(quantity) FROM inventory_transactions WHERE type='out'").fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_orders WHERE id=?', (order_id,)).fetchone()[0], 3)
        self.assertEqual(shipment_count, 1)
        self.assertEqual(balance, 0)

    def test_shipment_create_edit_and_delete_keep_inventory_in_sync(self):
        manual_id, order_id = self.create_product_and_order()
        location_id = self.stock_product(manual_id, 10)

        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-07-16",
                "order_id": [str(order_id)],
                "shipped_quantity": ["3"],
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute("SELECT * FROM product_order_shipments").fetchone()
            self.assertEqual(app.inventory_balance_for_location(conn, manual_id, location_id), 7)
            inventory_out = conn.execute(
                "SELECT * FROM inventory_transactions WHERE related_order_id = ?",
                (app.shipment_inventory_related_id(shipment["id"]),),
            ).fetchall()
        self.assertEqual(sum(row["quantity"] for row in inventory_out), 3)

        edit_response = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/edit",
            data={
                "shipped_at": "2026-07-16",
                "shipped_quantity": "4",
                "logistics_no": "SF001",
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(app.inventory_balance_for_location(conn, manual_id, location_id), 6)
            deducted = app.shipment_inventory_deducted_quantity(conn, shipment["id"])
        self.assertEqual(deducted, 4)

        delete_response = self.client.post(f"/admin/shipped-orders/{shipment['id']}/delete")
        self.assertEqual(delete_response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(app.inventory_balance_for_location(conn, manual_id, location_id), 10)
            self.assertEqual(app.shipment_inventory_deducted_quantity(conn, shipment["id"]), 0)

    def test_approved_shipment_plan_deducts_inventory_and_leaves_open_plan_list(self):
        manual_id, order_id = self.create_product_and_order()
        location_id = self.stock_product(manual_id, 10)
        self.client.post(
            "/admin/orders/shipment-plans",
            data={"order_id": [str(order_id)], "planned_ship_at": "2026-07-16"},
        )
        with app.get_db() as conn:
            plan = conn.execute("SELECT * FROM shipment_plans").fetchone()
            item = conn.execute("SELECT * FROM shipment_plan_items WHERE plan_id = ?", (plan["id"],)).fetchone()

        response = self.client.post(
            f"/admin/shipped-orders/plans/{plan['id']}/approve",
            data={
                "shipped_at": "2026-07-16",
                "plan_item_id": [str(item["id"])],
                "shipped_quantity": ["4"],
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            updated_plan = conn.execute("SELECT * FROM shipment_plans WHERE id = ?", (plan["id"],)).fetchone()
            shipment_count = conn.execute("SELECT COUNT(*) AS c FROM product_order_shipments").fetchone()["c"]
            open_plans = app.fetch_open_shipment_plans(conn)
            balance = app.inventory_balance_for_location(conn, manual_id, location_id)
        self.assertEqual(updated_plan["status"], "已完成")
        self.assertEqual(shipment_count, 1)
        self.assertEqual(open_plans, [])
        self.assertEqual(balance, 6)
