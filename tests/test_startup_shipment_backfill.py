import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import shipping_workflow
from tests import test_delivery_note_items as delivery_fixtures


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


class LegacyWorkflowStartupTests(delivery_fixtures.DeliveryPersistenceFixture):
    """A pre-upgrade database, including real claims and source references."""

    TABLES = ('delivery_note_sources', 'finance_invoice_items', 'reconciliation_statement_items')

    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.paths = patch.multiple(app, **{
            name: Path(self.files.name) / name.lower()
            for name in ('DATA_DIR', 'MANUALS_DIR', 'SIGNATURES_DIR',
                         'RECONCILIATION_SIGNATURES_DIR', 'SHIPMENT_IMAGES_DIR',
                         'INSPECTION_REPORTS_DIR', 'PRODUCTION_DRAWINGS_DIR')
        })
        self.paths.start()
        self.addCleanup(self.paths.stop)
        super().setUp()
        self.addCleanup(super().tearDown)
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session.update(
                admin_logged_in=True,
                admin_username='admin',
                admin_role='admin',
                production_followup_csrf_token='legacy-startup-csrf',
            )
        self.client.environ_base['HTTP_X_CSRF_TOKEN'] = 'legacy-startup-csrf'
        with app.get_db() as conn:
            delivery_fixtures.DeliverySourceMigrationTests.seed_legacy(self, conn)
            # Old imports wrote the matching drawing number but no specification.
            conn.execute("UPDATE manuals SET supplier='', sku='OLD-IMPORT', unit='PCS', remark='历史备注' WHERE id=?",
                         (self.manual_a,))
            conn.execute("""INSERT INTO product_assembly_components
                (manual_id,assembly_drawing_no,quantity_per_set,sort_order,created_at,updated_at)
                VALUES (?,'ASM-A',1,0,'2026-09-01','2026-09-01')""", (self.manual_a,))
            order_id = conn.execute('SELECT order_id FROM product_order_shipments WHERE id=?',
                                    (self.ordinary_a_cny,)).fetchone()[0]
            conn.execute("""INSERT INTO assembly_shipment_allocations
                (item_id,order_id,quantity,created_at) VALUES (?,?,4,'2026-09-05')""",
                (self.assembly_a_cny, order_id))
            conn.execute("UPDATE product_orders SET shipped_quantity=11, shipped_at='2026-09-05', inventory_received_quantity=20 WHERE id=?",
                         (order_id,))
            for followup_id, laser, bending in [(71, '2026-09-01T08:00:00', '2026-09-01T09:00:00'),
                                               (83, '', '')]:
                conn.execute("""INSERT INTO production_followups
                    (id,batch_no,customer,manual_id,ordered_at,drawing_no,product_name,
                     laser_completed_at,bending_completed_at,welding_completed_at,created_by,created_at,updated_at)
                    VALUES (?,?,'客户A',?,'2026-09-01','FA-100','历史产品',?,?, '',
                            'legacy-worker','2026-09-01','2026-09-01')""",
                    (followup_id, f'LEGACY-CARD-{followup_id}', self.manual_a, laser, bending))
            self.invoice = conn.execute('SELECT id FROM finance_invoices').fetchone()[0]
            self.statement = conn.execute('SELECT id FROM reconciliation_statements').fetchone()[0]
            app.mark_finance_invoice_invoiced(conn, self.invoice, 'INV-LEGACY', '2026-09-06', '历史开票', 'finance')
            conn.execute("UPDATE reconciliation_statements SET status='confirmed', confirmed_name='历史签收人', confirmed_at='2026-09-06' WHERE id=?",
                         (self.statement,))
            # Remove the new schema so startup must perform every upgrade, not
            # merely re-run against already-modern empty tables.
            for table in ('manual_process_steps', 'manual_process_configs',
                          'production_followup_process_steps', 'delivery_note_items',
                          'supplemental_shipment_images', 'supplemental_shipments'):
                conn.execute(f'DROP TABLE {table}')
            conn.execute('ALTER TABLE production_followups DROP COLUMN process_snapshot_created')
            conn.execute('ALTER TABLE assembly_shipment_items DROP COLUMN remark')

    def tearDown(self):
        # Registered cleanups also run if legacy fixture construction fails.
        pass

    @staticmethod
    def database_rows(conn):
        return {
            row['name']: [dict(item) for item in conn.execute(f'SELECT * FROM "{row["name"]}" ORDER BY rowid')]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        }

    def test_full_legacy_startup_twice_preserves_rows_claims_routes_and_triggers(self):
        with app.get_db() as conn:
            before = self.database_rows(conn)
            self.assertNotIn('production_followup_process_steps', before)
            self.assertNotIn('delivery_note_items', before)
            for table in self.TABLES:
                self.assertNotIn('supplemental', conn.execute(
                    'SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0])

        app.init_db()
        with app.get_db() as conn:
            first_rows = self.database_rows(conn)
            first_schema = [tuple(row) for row in conn.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(self.database_rows(conn), first_rows)
            self.assertEqual([tuple(row) for row in conn.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')], first_schema)
            for table, old_rows in before.items():
                # New columns are additive; every old value and row ID survives.
                current = first_rows[table]
                self.assertEqual(len(current), len(old_rows), table)
                self.assertEqual([{key: row[key] for key in old} for row, old in zip(current, old_rows)],
                                 old_rows, table)
            self.assertEqual([tuple(row) for row in conn.execute('''SELECT followup_id,name,sort_order,completed_at
                FROM production_followup_process_steps ORDER BY followup_id,sort_order''')], [
                    (71, '激光', 0, '2026-09-01T08:00:00'), (71, '折弯', 1, '2026-09-01T09:00:00'),
                    (71, '焊接', 2, ''), (83, '激光', 0, ''), (83, '折弯', 1, ''), (83, '焊接', 2, '')])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_note_items').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            refs = [('ordinary', self.ordinary_a_cny), ('assembly_item', self.assembly_a_cny)]
            for kind, source_id in refs:
                self.assertTrue(app.finance_source_is_claimed(conn, kind, source_id))
                self.assertTrue(app.reconciliation_source_is_claimed(conn, kind, source_id))
            for check in (app.assert_finance_sources_mutable, app.assert_reconciliation_sources_mutable):
                with self.assertRaises(ValueError):
                    check(conn, refs)
            for note_id, quantity in [(17, 2), (23, 4)]:
                note = shipping_workflow.load_delivery_note(conn, note_id)
                self.assertEqual(note['invalidated'], 0)
                self.assertEqual(note['items'][0]['shipped_quantity'], quantity)

        for route, expected in [('/admin/products', 'FA-100'),
                                (f'/manual/{self.manual_a}/technical', '默认生产工艺'),
                                ('/admin/production-followups/71/process-card', '折弯'),
                                ('/admin/shipped-orders', 'SO-A'),
                                (f'/admin/finance/{self.invoice}', 'INV-LEGACY'),
                                (f'/admin/reconciliation-statements/{self.statement}', '历史签收人')]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.get_data(as_text=True))
        for note_id in (17, 23):
            response = self.client.get(f'/admin/delivery-notes/{note_id}.pdf')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data.startswith(b'%PDF'))
        deleted = self.client.post('/admin/products/delete-batch', data={
            'manual_id': [str(self.manual_b), str(self.manual_a)]}, follow_redirects=True)
        self.assertIn('整批未删除', deleted.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0], 2)
            # Exercise both legacy source triggers without permanently mutating
            # the locked fixtures (HTTP paths above already verify lock refusal).
            for table, source_id, note_id in [('product_order_shipments', self.ordinary_a_cny, 17),
                                              ('assembly_shipment_batches', self.batch_a, 23)]:
                conn.execute('SAVEPOINT trigger_probe')
                conn.execute("UPDATE delivery_notes SET updated_at='old' WHERE id=?", (note_id,))
                conn.execute(f"UPDATE {table} SET logistics_no='probe' WHERE id=?", (source_id,))
                self.assertNotEqual(conn.execute('SELECT updated_at FROM delivery_notes WHERE id=?', (note_id,)).fetchone()[0], 'old')
                conn.execute(f'DELETE FROM {table} WHERE id=?', (source_id,))
                self.assertEqual(conn.execute('SELECT invalidated FROM delivery_notes WHERE id=?', (note_id,)).fetchone()[0], 1)
                conn.execute('ROLLBACK TO trigger_probe')
                conn.execute('RELEASE trigger_probe')
