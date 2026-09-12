"""Batch queries must use customer links without splitting shared stock."""
import sqlite3
import unittest

from inventory_batch import InventoryConflict, apply_batch, ensure_batch_table, load_batch_data


class SharedBatchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE manuals (id INTEGER PRIMARY KEY, product_name TEXT,
                drawing_no TEXT, sku TEXT, default_location_id INTEGER, customer TEXT);
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE product_customers (manual_id INTEGER, customer_id INTEGER);
            CREATE VIEW product_customer_names AS
                SELECT pc.manual_id, c.id AS customer_id, c.name AS customer
                FROM product_customers pc JOIN customers c ON c.id=pc.customer_id
                UNION SELECT m.id, c.id, TRIM(m.customer) FROM manuals m
                LEFT JOIN customers c ON c.name=TRIM(m.customer)
                WHERE TRIM(COALESCE(m.customer,''))!='';
            CREATE TABLE warehouse_locations (id INTEGER PRIMARY KEY, name TEXT, code TEXT, enabled INTEGER);
            CREATE TABLE inventory_balances (manual_id INTEGER, location_id INTEGER, quantity INTEGER);
            CREATE TABLE product_assembly_components (id INTEGER PRIMARY KEY,
                manual_id INTEGER, assembly_drawing_no TEXT, quantity_per_set INTEGER, customer TEXT);
            CREATE TABLE product_orders (id INTEGER PRIMARY KEY, manual_id INTEGER,
                order_no TEXT, customer TEXT, quantity INTEGER, inventory_received_quantity INTEGER,
                assembly_drawing_no TEXT);
            CREATE TABLE test_ledger (manual_id INTEGER, customer TEXT, quantity INTEGER);
            INSERT INTO manuals VALUES (1,'共享后盖','P1','',1,'客户甲');
            INSERT INTO customers VALUES (1,'客户甲'),(2,'客户乙'),(3,'客户丙');
            INSERT INTO product_customers VALUES (1,1),(1,2);
            INSERT INTO warehouse_locations VALUES (1,'成品区','A01',1);
            INSERT INTO inventory_balances VALUES (1,1,10);
            INSERT INTO product_assembly_components VALUES
                (1,1,'DZ30',2,''),(2,1,'DZ30',5,'客户乙'),(3,1,'LEGACY',3,'');
            INSERT INTO product_orders VALUES (1,1,'SO-B','客户乙',50,0,'DZ30');
        """)
        ensure_batch_table(self.conn)

    def test_linked_customer_sees_one_shared_balance_and_own_bom_count(self):
        data = load_batch_data(self.conn, 'inbound', {'customer': '客户乙', 'assembly': 'DZ30'})
        self.assertEqual([(r['manual_id'], r['customer'], r['quantity_per_set'], r['total_stock'])
                          for r in data['rows']], [(1, '客户乙', 5, 10)])
        self.assertEqual(len(load_batch_data(self.conn, 'inbound', {})['rows']), 1)

    def test_customer_options_include_link_without_orders_or_legacy_name(self):
        self.conn.execute('INSERT INTO product_customers VALUES (1,3)')
        data = load_batch_data(self.conn, 'inbound', {})
        self.assertEqual(data['customers'], ['客户丙', '客户乙', '客户甲'])

    def test_legacy_bom_does_not_leak_to_secondary_customer(self):
        data = load_batch_data(self.conn, 'inbound', {'customer': '客户乙', 'assembly': 'LEGACY'})
        self.assertEqual(data['rows'], [])
        self.assertNotIn('LEGACY', data['assemblies'])
        primary = load_batch_data(self.conn, 'inbound', {'customer': '客户甲', 'assembly': 'LEGACY'})
        self.assertEqual(primary['rows'][0]['quantity_per_set'], 3)

    def test_order_bom_count_uses_order_customer_for_inbound_and_adjustment(self):
        for mode in ('inbound', 'adjust'):
            with self.subTest(mode=mode):
                data = load_batch_data(self.conn, mode, {'order_no': 'SO-B', 'assembly': 'DZ30'})
                self.assertEqual([(r['customer'], r['quantity_per_set']) for r in data['rows']], [('客户乙', 5)])

    def test_assembly_only_filter_can_find_secondary_customer_bom(self):
        self.conn.execute("INSERT INTO product_assembly_components VALUES (4,1,'SECONDARY',7,'客户乙')")
        data = load_batch_data(self.conn, 'inbound', {'assembly': 'SECONDARY'})
        self.assertEqual([(r['manual_id'], r['customer'], r['quantity_per_set'])
                          for r in data['rows']], [(1, '客户乙', 7)])

    def transact(self, conn, kind, manual_id, delta, **details):
        conn.execute('UPDATE inventory_balances SET quantity=quantity+? WHERE manual_id=? AND location_id=?',
                     (delta, manual_id, details['to_location_id']))
        conn.execute('INSERT INTO test_ledger VALUES (?,?,?)', (manual_id, details['customer'], delta))

    def test_linked_customer_inbound_updates_shared_stock_and_customer_ledger(self):
        row = load_batch_data(self.conn, 'inbound', {})['rows'][0]
        row['customer'] = '客户乙'
        result = apply_batch(self.conn, {'mode': 'inbound', 'rows': [row]},
                            {'rows': [{'key': row['key'], 'location_id': 1, 'quantity': 4}]},
                            'token', 'admin', self.transact)
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(self.conn.execute('SELECT quantity FROM inventory_balances').fetchone()[0], 14)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM test_ledger').fetchone()), (1, '客户乙', 4))

    def test_removed_customer_link_rejects_stale_inbound_snapshot(self):
        row = load_batch_data(self.conn, 'inbound', {})['rows'][0]
        row['customer'] = '客户乙'
        self.conn.execute('DELETE FROM product_customers WHERE customer_id=2')
        with self.assertRaises(InventoryConflict):
            apply_batch(self.conn, {'mode': 'inbound', 'rows': [row]},
                        {'rows': [{'key': row['key'], 'location_id': 1, 'quantity': 4}]},
                        'token', 'admin', self.transact)
        self.assertEqual(self.conn.execute('SELECT quantity FROM inventory_balances').fetchone()[0], 10)

    def test_product_without_customer_still_allows_general_inbound(self):
        self.conn.execute("UPDATE manuals SET customer='' WHERE id=1")
        self.conn.execute('DELETE FROM product_customers')
        row = load_batch_data(self.conn, 'inbound', {})['rows'][0]
        result = apply_batch(self.conn, {'mode': 'inbound', 'rows': [row]},
                            {'rows': [{'key': row['key'], 'location_id': 1, 'quantity': 4}]},
                            'token', 'admin', self.transact)
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(self.conn.execute('SELECT quantity FROM inventory_balances').fetchone()[0], 14)
