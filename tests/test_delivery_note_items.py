"""Real SQLite migration and immutable delivery-document contracts."""

import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

import app
import shipping_workflow as shipping
from tests import test_finance as finance_tests


class DeliveryPersistenceFixture(finance_tests.FinanceDomainTestCase):
    def operation(self, conn, token='snapshot-operation'):
        return shipping.start_delivery_operation(conn, token, token, 'shipper')

    def note(self, conn, **kwargs):
        return shipping.create_delivery_notes(
            conn, self.operation(conn), [('ordinary', self.ordinary_a_cny)],
            {}, 'shipper', **kwargs,
        )[0]


class DeliveryNoteItemsTests(DeliveryPersistenceFixture):
    def test_snapshot_keeps_live_signature_metadata_without_reloading_product_fields(self):
        with app.get_db() as conn:
            note_id = self.note(conn)
            conn.execute("""UPDATE product_order_shipments SET signature_status='已签收',
                signature_image='signed.png', signed_at='2026-09-10', shipped_quantity=9
                WHERE id=?""", (self.ordinary_a_cny,))
            item = shipping.load_delivery_note(conn, note_id)['items'][0]
            self.assertEqual((item['signature_status'], item['signature_image'], item['signed_at']),
                             ('已签收', 'signed.png', '2026-09-10'))
            self.assertEqual(item['shipped_quantity'], 2)

    def test_zero_document_quantity_has_zero_amount_even_when_product_is_unpriced(self):
        from pricing import line_total_minor
        self.assertEqual(line_total_minor(None, 0), 0)
        self.assertIsNone(line_total_minor(None, 2))

    def test_new_note_freezes_product_and_quantity_but_legacy_remains_dynamic(self):
        with app.get_db() as conn:
            note_id = self.note(conn)
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM delivery_note_items WHERE note_id=?', (note_id,)
            ).fetchone()[0], 1)
            conn.execute("UPDATE manuals SET drawing_no='CHANGED', product_name='Changed' WHERE id=?", (self.manual_a,))
            conn.execute('UPDATE product_order_shipments SET shipped_quantity=9 WHERE id=?', (self.ordinary_a_cny,))
            item = shipping.load_delivery_note(conn, note_id)['items'][0]
            self.assertEqual((item['drawing_no'], item['product_name'], item['shipped_quantity']),
                             ('FA-100', '财务产品A', 2))
            conn.execute('DELETE FROM delivery_note_items WHERE note_id=?', (note_id,))
            legacy = shipping.load_delivery_note(conn, note_id)['items'][0]
            self.assertEqual((legacy['drawing_no'], legacy['shipped_quantity']), ('CHANGED', 9))

    def test_ordered_zero_line_without_source_and_500_character_remark_are_saved(self):
        lines = [
            dict(customer='客户A', manual_id=self.manual_a, quantity=0,
                 drawing_no='ZERO', product_name='本次不发', specification='', unit='件',
                 order_no='SO-A', assembly_drawing_no='', remark='注' * 500),
            dict(customer='客户A', manual_id=self.manual_a, quantity=2,
                 source_type='ordinary', source_id=self.ordinary_a_cny,
                 drawing_no='FROZEN', product_name='原产品', specification='原规格',
                 unit='套', order_no='订单快照', assembly_drawing_no='', remark='发两件'),
        ]
        with app.get_db() as conn:
            note_id = self.note(conn, display_lines=lines)
            note = shipping.load_delivery_note(conn, note_id)
            self.assertEqual([(i['drawing_no'], i['shipped_quantity'], i['remark']) for i in note['items']],
                             [('ZERO', 0, '注' * 500), ('FROZEN', 2, '发两件')])
            self.assertEqual([i['sort_order'] for i in note['items']], [0, 1])
            self.assertIsNone(note['items'][0]['source_id'])
            self.assertEqual(len(note['sources']), 1)
            self.assertEqual(note['invalidated'], 0)

    def test_snapshot_input_rejects_bad_quantity_remark_customer_and_source(self):
        invalid = [dict(quantity=-1), dict(quantity=1.5), dict(quantity=True),
                   dict(remark='x' * 501), dict(customer='客户B'),
                   dict(source_type='ordinary', source_id=self.ordinary_b_cny),
                   dict(source_type='ordinary', source_id=None), dict(quantity=0, source_id=1)]
        for changes in invalid:
            with self.subTest(changes=changes), app.get_db() as conn:
                operation_id = self.operation(conn, str(changes))
                line = dict(customer='客户A', manual_id=self.manual_a, quantity=2,
                            drawing_no='FA-100', product_name='A', remark='')
                line.update(changes)
                with self.assertRaises(ValueError):
                    shipping.create_delivery_notes(conn, operation_id,
                        [('ordinary', self.ordinary_a_cny)], {}, 'shipper', display_lines=[line])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_notes').fetchone()[0], 0)

    def test_multicustomer_lines_keep_order_inside_authoritative_source_groups(self):
        with app.get_db() as conn:
            ids = shipping.create_delivery_notes(conn, self.operation(conn),
                [('ordinary', self.ordinary_b_cny), ('ordinary', self.ordinary_a_cny)], {}, 'shipper',
                display_lines=[dict(customer='客户B', quantity=0, drawing_no='B-zero'),
                               dict(customer='客户A', quantity=2, drawing_no='A'),
                               dict(customer='客户B', quantity=3, drawing_no='B')])
            notes = [shipping.load_delivery_note(conn, note_id) for note_id in ids]
            self.assertEqual([(n['customer'], [i['drawing_no'] for i in n['items']]) for n in notes],
                             [('客户A', ['A']), ('客户B', ['B-zero', 'B'])])

    def test_snapshot_does_not_bypass_source_ownership_or_deletion_invalidation(self):
        with app.get_db() as conn:
            note_id = self.note(conn)
            conn.execute("UPDATE product_orders SET customer='客户B' WHERE id=(SELECT order_id FROM product_order_shipments WHERE id=?)", (self.ordinary_a_cny,))
            self.assertEqual(shipping.load_delivery_note(conn, note_id)['invalidated'], 1)
            conn.execute('DELETE FROM product_order_shipments WHERE id=?', (self.ordinary_a_cny,))
            note = shipping.load_delivery_note(conn, note_id)
            self.assertEqual(note['invalidated'], 1)
            self.assertEqual(note['items'][0]['shipped_quantity'], 2)

    def test_snapshot_write_failure_rolls_back_operation_sources_and_notes(self):
        with app.get_db() as conn:
            conn.execute("""CREATE TRIGGER reject_snapshot BEFORE INSERT ON delivery_note_items
                BEGIN SELECT RAISE(ABORT, 'snapshot failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'snapshot failure'), app.get_db() as conn:
            self.note(conn)
        with app.get_db() as conn:
            for table in ('delivery_operations', 'delivery_notes', 'delivery_note_sources', 'delivery_note_items'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)


class DeliverySourceMigrationTests(DeliveryPersistenceFixture):
    TABLES = ('delivery_note_sources', 'finance_invoice_items', 'reconciliation_statement_items')

    def seed_legacy(self, conn):
        # Build genuine old CHECK constraints, even when startup already knows
        # supplemental. No migration helper is used to create the old fixture.
        for table in self.TABLES:
            schema = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
            objects = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))]
            conn.execute(f'DROP TABLE {table}')
            conn.execute(schema.replace(", 'supplemental'", ''))
            for statement in objects:
                conn.execute(statement)
        conn.execute('CREATE TABLE migration_events (source_type TEXT, source_id INTEGER)')
        for table in self.TABLES:
            conn.execute(f'CREATE INDEX custom_{table} ON {table}(source_id) WHERE source_id > 0')
            conn.execute(f'''CREATE TRIGGER audit_{table} AFTER INSERT ON {table}
                BEGIN INSERT INTO migration_events VALUES (NEW.source_type, NEW.source_id); END''')
        for note_id, kind, sid in [(17, 'ordinary', self.ordinary_a_cny), (23, 'assembly', self.batch_a)]:
            operation_id = self.operation(conn, f'legacy-{note_id}')
            conn.execute('''INSERT INTO delivery_notes
                (id,document_no,operation_id,customer,recipient_name,recipient_phone,address,operator,created_at,updated_at)
                VALUES (?, ?, ?, ?, '', '', '', 'old', '', '')''',
                (note_id, f'DN-OLD-{note_id}', operation_id, '客户A'))
            conn.execute('INSERT INTO delivery_note_sources VALUES (?, ?, ?)', (note_id, kind, sid))
        refs = [('ordinary', self.ordinary_a_cny), ('assembly_item', self.assembly_a_cny)]
        app.create_finance_invoice(conn, self.customer_a, refs, 'finance')
        app.create_reconciliation_statement(conn, refs, 'finance')
        for table in ('finance_invoice_items', 'reconciliation_statement_items'):
            conn.execute(f"UPDATE {table} SET id=CASE source_type WHEN 'ordinary' THEN 31 ELSE 37 END")
        # A deleted high ID must not be reused after the rebuild.
        conn.execute("UPDATE sqlite_sequence SET seq=999 WHERE name IN ('finance_invoice_items','reconciliation_statement_items')")

    def test_startup_preserves_old_rows_constraints_indexes_triggers_and_sequences(self):
        historical_tables = ('delivery_notes', 'delivery_operations', 'product_order_shipments',
                             'assembly_shipment_batches', 'assembly_shipment_items',
                             'assembly_shipment_allocations')
        with app.get_db() as conn:
            self.seed_legacy(conn)
            before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in self.TABLES}
            historical = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')]
                          for t in historical_tables}
            objects = [tuple(r) for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('index','trigger') AND sql IS NOT NULL ORDER BY name")]
        app.init_db()
        app.init_db()
        with app.get_db() as conn:
            for table in historical_tables:
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY id')], historical[table])
            for table in self.TABLES:
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')], before[table])
                conn.execute(f"UPDATE {table} SET source_type='supplemental' WHERE source_type='ordinary'")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f"UPDATE {table} SET source_type='invalid'")
                self.assertIsNotNone(conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (f'custom_{table}',)).fetchone())
                self.assertIsNotNone(conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (f'audit_{table}',)).fetchone())
            for name, sql in objects:
                self.assertEqual(conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0], sql)
            self.assertEqual([r[0] for r in conn.execute("SELECT seq FROM sqlite_sequence WHERE name IN ('finance_invoice_items','reconciliation_statement_items')")], [999, 999])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM migration_events').fetchone()[0], 6)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            conn.execute("INSERT INTO delivery_note_sources VALUES (17, 'supplemental', 999)")
            self.assertEqual(tuple(conn.execute('SELECT * FROM migration_events ORDER BY rowid DESC LIMIT 1').fetchone()), ('supplemental', 999))

    def test_copy_mismatch_aborts_before_swap_and_retry_preserves_data(self):
        for corruption in ('DELETE', 'UPDATE'):
            with self.subTest(corruption=corruption), app.get_db() as conn:
                # The helper's fingerprint is fault-injected only after the
                # copy: SQL still copies real rows and corruption affects the
                # temporary SQLite table, not a fake return value.
                if not hasattr(shipping, '_table_fingerprint'):
                    self.fail('verified source-table migration is not implemented')
                if corruption == 'DELETE':
                    self.seed_legacy(conn)
                original = shipping._table_fingerprint
                before = [tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')]
                def corrupt_copy(connection, table):
                    if table != 'delivery_note_sources':
                        connection.execute(f'{corruption} FROM "{table}"' if corruption == 'DELETE'
                                           else f'UPDATE "{table}" SET source_id=source_id+100')
                    return original(connection, table)
                with patch.object(shipping, '_table_fingerprint', side_effect=corrupt_copy):
                    with self.assertRaisesRegex(RuntimeError, 'verification'):
                        shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')
                self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')], before)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%supplemental_migration%'").fetchone()[0], 0)
        with app.get_db() as conn:
            shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')
            conn.execute("UPDATE delivery_note_sources SET source_type='supplemental' WHERE source_type='ordinary'")

    def test_copy_digest_distinguishes_blob_from_text_in_preserved_extension_fields(self):
        with app.get_db() as conn:
            self.seed_legacy(conn)
            conn.execute('ALTER TABLE delivery_note_sources ADD COLUMN legacy_payload BLOB')
            conn.execute("UPDATE delivery_note_sources SET legacy_payload=x'6162'")
            fingerprint = shipping._table_fingerprint
            def corrupt_type(connection, table):
                if table != 'delivery_note_sources':
                    connection.execute(f'''UPDATE "{table}" SET legacy_payload='6162' ''')
                return fingerprint(connection, table)
            with patch.object(shipping, '_table_fingerprint', side_effect=corrupt_type):
                with self.assertRaisesRegex(RuntimeError, 'verification'):
                    shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')
            self.assertEqual(conn.execute('SELECT legacy_payload FROM delivery_note_sources LIMIT 1').fetchone()[0], b'ab')

    def test_index_recreation_failure_rolls_back_swap_and_respects_outer_transaction(self):
        with app.get_db() as conn:
            self.seed_legacy(conn)
            before = [tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')]
            def deny_index(action, arg1, arg2, db, trigger):
                if action == sqlite3.SQLITE_CREATE_INDEX and arg1 == 'custom_delivery_note_sources':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(deny_index)
            with self.assertRaises(sqlite3.DatabaseError):
                shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')
            conn.set_authorizer(None)
            self.assertTrue(conn.in_transaction)
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')], before)
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='custom_delivery_note_sources'").fetchone())
            shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')

    def test_startup_failure_in_second_rebuild_rolls_back_first_and_can_retry(self):
        with app.get_db() as conn:
            self.seed_legacy(conn)
            before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in self.TABLES}
        get_db = app.get_db
        @contextmanager
        def fail_reconciliation_index():
            with get_db() as conn:
                conn.set_authorizer(lambda action, name, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX
                    and name == 'custom_reconciliation_statement_items' else sqlite3.SQLITE_OK)
                yield conn
        with patch.object(app, 'get_db', fail_reconciliation_index):
            with self.assertRaises(sqlite3.DatabaseError):
                app.init_db()
        with app.get_db() as conn:
            for table in self.TABLES:
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')], before[table])
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f"UPDATE {table} SET source_type='supplemental'")
        app.init_db()
        with app.get_db() as conn:
            for table in self.TABLES:
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')], before[table])

    def test_process_interruption_after_swap_recovers_original_rows_on_reopen(self):
        with app.get_db() as conn:
            self.seed_legacy(conn)
            before = [tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')]
        process = subprocess.run([sys.executable, '-c', '''
import os, sqlite3, sys
from shipping_workflow import ensure_supplemental_source_type
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
ensure_supplemental_source_type(conn, 'delivery_note_sources')
os._exit(9)
''', str(app.DB_PATH)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 9, process.stderr)
        with app.get_db() as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')], before)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE delivery_note_sources SET source_type='supplemental'")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%supplemental_migration%'").fetchone()[0], 0)
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM delivery_note_sources')], before)

    def test_rebuild_preserves_foreign_keys_external_views_and_trigger_behavior(self):
        with app.get_db() as conn:
            self.seed_legacy(conn)
            conn.execute('CREATE VIEW historical_delivery_sources AS SELECT * FROM delivery_note_sources')
        with app.get_db() as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            shipping.ensure_supplemental_source_type(conn, 'delivery_note_sources')
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA legacy_alter_table').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM historical_delivery_sources').fetchone()[0], 2)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check(delivery_note_sources)').fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO delivery_note_sources VALUES (999, 'supplemental', 55)")
            conn.execute('DELETE FROM product_order_shipments WHERE id=?', (self.ordinary_a_cny,))
            self.assertEqual(conn.execute('SELECT invalidated FROM delivery_notes WHERE id=17').fetchone()[0], 1)
