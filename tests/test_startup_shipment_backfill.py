import tempfile
import unittest
from pathlib import Path

import app


class StartupShipmentBackfillTests(unittest.TestCase):
    def setUp(self):
        original_db, original_ready = app.DB_PATH, app.DATABASE_READY
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, app, 'DB_PATH', original_db)
        self.addCleanup(setattr, app, 'DATABASE_READY', original_ready)
        app.DB_PATH = Path(self.temp.name) / 'startup.db'
        app.DATABASE_READY = False
        app.init_db()
        with app.get_db() as conn:
            self.product = conn.execute("""INSERT INTO manuals
                (product_name,customer,model,category,version,filename,original_filename,created_at,updated_at)
                VALUES ('面板','客户甲','','','','','','2026-09-08','2026-09-08')""").lastrowid
            self.order = conn.execute("""INSERT INTO product_orders
                (manual_id,order_no,ordered_at,quantity,customer,planned_ship_at,created_at,updated_at,
                 shipped_quantity,shipped_at,inventory_received_quantity)
                VALUES (?,'SO-LEGACY','2026-09-08',20,'客户甲','','2026-09-08','2026-09-08',10,'2026-09-08',15)""",
                (self.product,)).lastrowid

    def allocate(self, quantity, order_id):
        with app.get_db() as conn:
            batch = conn.execute("""INSERT INTO assembly_shipment_batches
                (customer,assembly_drawing_no,set_quantity,shipped_at,logistics_no,created_by,created_at,updated_at)
                VALUES ('客户甲','DZ-30',1,'2026-09-08','','admin','2026-09-08','2026-09-08')""").lastrowid
            item = conn.execute("""INSERT INTO assembly_shipment_items
                (batch_id,manual_id,drawing_no,product_name,quantity_per_set,calculated_quantity,
                 shipped_quantity,inventory_deducted_quantity,inventory_shortage_quantity,created_at,updated_at)
                VALUES (?,?,'P1','面板',1,?,?,0,?,'2026-09-08','2026-09-08')""",
                (batch,self.product,quantity,quantity,quantity)).lastrowid
            conn.execute("""INSERT INTO assembly_shipment_allocations
                (item_id,order_id,quantity,created_at) VALUES (?,?,?,'2026-09-08')""",
                (item,order_id,quantity))

    def shipment_rows(self):
        with app.get_db() as conn:
            return [tuple(row) for row in conn.execute(
                'SELECT order_id,shipped_quantity,shipped_at FROM product_order_shipments ORDER BY id')]

    def test_restart_does_not_duplicate_fully_assembly_shipped_order(self):
        self.allocate(4, self.order)
        self.allocate(6, self.order)
        for _ in range(2):
            app.init_db()
            self.assertEqual(self.shipment_rows(), [])
        with app.get_db() as conn:
            self.assertEqual(tuple(conn.execute(
                'SELECT shipped_quantity,inventory_received_quantity FROM product_orders WHERE id=?',
                (self.order,)).fetchone()), (10,15))
            self.assertEqual(conn.execute('SELECT SUM(quantity) FROM assembly_shipment_allocations').fetchone()[0], 10)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM inventory_transactions').fetchone()[0], 0)

    def test_partial_assembly_coverage_backfills_only_unrecorded_legacy_quantity(self):
        self.allocate(4, self.order)
        for _ in range(2):
            app.init_db()
            self.assertEqual(self.shipment_rows(), [(self.order,6,'2026-09-08')])

    def test_existing_ordinary_records_are_not_rewritten(self):
        self.allocate(6, self.order)
        with app.get_db() as conn:
            conn.execute("""INSERT INTO product_order_shipments
                (order_id,shipped_quantity,shipped_at,created_at,unit_price_minor,remark)
                VALUES (?,4,'2026-09-08','2026-09-08',1234,'保留')""", (self.order,))
            before = [tuple(row) for row in conn.execute('SELECT * FROM product_order_shipments')]
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM product_order_shipments')], before)

    def test_legacy_database_without_assembly_table_still_backfills_once(self):
        with app.get_db() as conn:
            conn.execute('DROP TABLE assembly_shipment_allocations')
        for _ in range(2):
            app.init_db()
            self.assertEqual(self.shipment_rows(), [(self.order,10,'2026-09-08')])

    def test_assembly_quantity_above_order_total_does_not_create_negative_or_duplicate_record(self):
        self.allocate(12, self.order)
        app.init_db()
        self.assertEqual(self.shipment_rows(), [])

    def test_unallocated_assembly_shipment_does_not_hide_legacy_order_shipment(self):
        self.allocate(10, None)
        app.init_db()
        self.assertEqual(self.shipment_rows(), [(self.order,10,'2026-09-08')])
