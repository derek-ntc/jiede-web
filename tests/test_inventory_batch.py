import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from datetime import datetime
from unittest.mock import patch

import app


class InventoryBatchTests(unittest.TestCase):
    def setUp(self):
        self.original_db, self.original_ready = app.DB_PATH, app.DATABASE_READY
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, app, 'DB_PATH', self.original_db)
        self.addCleanup(setattr, app, 'DATABASE_READY', self.original_ready)
        app.DB_PATH = Path(self.temp.name) / 'test.db'
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s.update(admin_logged_in=True, admin_username='admin', admin_role='admin')
        with app.get_db() as c:
            now = '2026-09-08T10:00:00'
            self.loc = c.execute("INSERT INTO warehouse_locations (name,code,enabled,created_at,updated_at) VALUES ('成品区','A01',1,?,?)", (now, now)).lastrowid
            self.loc2 = c.execute("INSERT INTO warehouse_locations (name,code,enabled,created_at,updated_at) VALUES ('备用区','B01',1,?,?)", (now, now)).lastrowid
            self.ids = []
            for name, customer, drawing in [('后盖','客户甲','P1'),('面板','客户甲','P2'),('异客产品','客户乙','P3')]:
                self.ids.append(c.execute("""INSERT INTO manuals
                    (product_name,customer,drawing_no,model,category,version,filename,original_filename,created_at,updated_at)
                    VALUES (?,?,?,'','','','','',?,?)""", (name,customer,drawing,now,now)).lastrowid)
            for product, assembly, count in [(self.ids[0],'DZ30',2),(self.ids[0],'DZ31',2),(self.ids[1],'DZ30',1),(self.ids[2],'DZ30',5)]:
                c.execute('INSERT INTO product_assembly_components (manual_id,assembly_drawing_no,quantity_per_set,sort_order,created_at,updated_at) VALUES (?,?,?,0,?,?)', (product,assembly,count,now,now))
            self.orders = []
            for product, qty in [(self.ids[0],20),(self.ids[1],10)]:
                self.orders.append(c.execute("""INSERT INTO product_orders
                    (manual_id,order_no,ordered_at,quantity,customer,assembly_drawing_no,planned_ship_at,created_at,updated_at)
                    VALUES (?,'SO-100','2026-09-08',?,'客户甲','DZ30','',?,?)""", (product,qty,now,now)).lastrowid)

    def page(self, mode='inbound', **filters):
        response = self.client.get('/admin/inventory/batch/' + mode, query_string=filters)
        self.assertEqual(response.status_code, 200)
        match = re.search(r'<script id="inventory-batch-data" type="application/json">(.*?)</script>', response.get_data(as_text=True), re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def payload(self, page, quantities, mode='inbound', remark=''):
        return dict(token=page['token'], csrf_token=page['csrf_token'], remark=remark,
                    rows=[dict(key=row['key'], location_id=self.loc, quantity=qty)
                          for row, qty in zip(page['rows'], quantities)])

    def post(self, payload, mode='inbound'):
        return self.client.post('/admin/inventory/batch/' + mode, json=payload)

    def quantities(self):
        with app.get_db() as c:
            return [app.inventory_total_for_manual(c, product) for product in self.ids]

    def test_assembly_filter_is_customer_scoped_and_shared_parts_are_unique(self):
        data = self.page(customer='客户甲', assembly='DZ30')
        self.assertEqual([(r['manual_id'], r['quantity_per_set']) for r in data['rows']], [(self.ids[0],2),(self.ids[1],1)])
        all_rows = self.page(customer='客户甲')['rows']
        self.assertEqual(len(all_rows), 2)

    def test_order_inbound_updates_stock_and_each_order_once_on_retry(self):
        page = self.page(customer='客户甲', order_no='SO-100')
        payload = self.payload(page, [6,3])
        first = self.post(payload)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(self.quantities(), [6,3,0])
        retry = self.post(payload)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json['duplicate'])
        self.assertEqual(self.quantities(), [6,3,0])
        with app.get_db() as c:
            rows = c.execute('SELECT inventory_received_quantity,inventory_status FROM product_orders ORDER BY id').fetchall()
            self.assertEqual([tuple(r) for r in rows], [(6,'部分入库'),(3,'部分入库')])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM inventory_transactions').fetchone()[0], 2)

    def test_invalid_second_row_rolls_back_entire_batch(self):
        page = self.page(customer='客户甲')
        payload = self.payload(page, [6,3])
        payload['rows'][1]['location_id'] = 9999
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(self.quantities(), [0,0,0])

    def test_rows_outside_preview_and_duplicate_rows_are_rejected(self):
        page = self.page(customer='客户甲')
        for key in ['p:' + str(self.ids[2]), page['rows'][0]['key']]:
            with self.subTest(key=key):
                payload = self.payload(page, [6,3])
                payload['rows'][1]['key'] = key
                self.assertEqual(self.post(payload).status_code, 400)
                self.assertEqual(self.quantities(), [0,0,0])

    def test_over_receipt_needs_explicit_confirmation(self):
        page = self.page(customer='客户甲', order_no='SO-100')
        payload = self.payload(page, [21,10])
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json['needs_confirmation'])
        self.assertEqual(self.quantities(), [0,0,0])
        payload['confirm_over_receipt'] = True
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.quantities(), [21,10,0])

    def test_adjust_uses_actual_count_per_location_and_does_not_update_orders(self):
        self.assertEqual(self.post(self.payload(self.page(customer='客户甲'), [10,5])).status_code, 200)
        page = self.page('adjust', customer='客户甲', location_id=self.loc)
        response = self.post(self.payload(page, [7,0], remark='盘点修正'), 'adjust')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.quantities(), [7,0,0])
        with app.get_db() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT quantity FROM inventory_transactions WHERE type='adjust' ORDER BY id")], [-3,-5])
            self.assertEqual([r[0] for r in c.execute('SELECT inventory_received_quantity FROM product_orders')], [0,0])

    def test_stale_adjustment_cannot_overwrite_new_inventory(self):
        page = self.page('adjust', customer='客户甲', location_id=self.loc)
        self.post(self.payload(self.page(customer='客户甲'), [10,5]))
        response = self.post(self.payload(page, [7,0], remark='盘点修正'), 'adjust')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.quantities(), [10,5,0])

    def test_adjust_requires_reason_and_strict_integer_quantity(self):
        page = self.page('adjust', customer='客户甲', location_id=self.loc)
        self.assertEqual(self.post(self.payload(page, [7,0]), 'adjust').status_code, 400)
        for qty in [-1, 1.2, True, '1e3', 2147483648]:
            with self.subTest(qty=qty):
                self.assertEqual(self.post(self.payload(page, [qty,0], remark='盘点'), 'adjust').status_code, 400)
        self.assertEqual(self.quantities(), [0,0,0])

    def test_quick_location_creation_normalizes_duplicate_and_updates_inbound_choices(self):
        page = self.page(customer='客户甲')
        response = self.client.post('/admin/inventory/locations/quick-create', json=dict(csrf_token=page['csrf_token'], code=' C01 ', name=' 新成品区 '))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['location']['code'], 'C01')
        duplicate = self.client.post('/admin/inventory/locations/quick-create', json=dict(csrf_token=page['csrf_token'], code='c01', name='另一区'))
        self.assertEqual(duplicate.status_code, 409)
        html = self.client.get('/admin/inventory/inbound').get_data(as_text=True)
        self.assertIn('C01 / 新成品区', html)
        self.assertIn('data-open-location', html)

    def test_csrf_and_inventory_permission_are_required(self):
        page = self.page(customer='客户甲')
        payload = self.payload(page, [1,1])
        payload['csrf_token'] = 'invalid'
        self.assertEqual(self.post(payload).status_code, 403)
        with self.client.session_transaction() as s:
            s.clear()
        self.assertEqual(self.post(self.payload(page, [1,1])).status_code, 302)
        self.assertEqual(self.quantities(), [0,0,0])

    def test_reused_token_with_changed_payload_is_rejected(self):
        page = self.page(customer='客户甲')
        payload = self.payload(page, [1,1])
        self.assertEqual(self.post(payload).status_code, 200)
        payload['rows'][0]['quantity'] = 2
        self.assertEqual(self.post(payload).status_code, 409)
        self.assertEqual(self.quantities(), [1,1,0])

    def test_all_locations_disabled_does_not_break_inbound_or_reenable_them(self):
        with app.get_db() as c:
            c.execute("UPDATE warehouse_locations SET code='DEFAULT' WHERE id=?", (self.loc,))
            c.execute('UPDATE warehouse_locations SET enabled=0')
        response = self.client.get('/admin/inventory/inbound')
        self.assertEqual(response.status_code, 200)
        with app.get_db() as c:
            self.assertEqual(c.execute('SELECT enabled FROM warehouse_locations WHERE id=?', (self.loc,)).fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM warehouse_locations WHERE enabled=1').fetchone()[0], 1)

    def test_frontend_quantity_rules_preserve_manual_values_and_reject_invalid_input(self):
        response = self.client.get('/static/inventory-batch-core.js')
        self.assertEqual(response.status_code, 200)
        result = subprocess.run(['node', '-e', '''
          const assert = require('node:assert/strict');
          const core = require('./static/inventory-batch-core.js');
          assert.equal(core.quantityForSets(2, '100'), 200);
          assert.equal(core.quantityForSets(1, '100'), 100);
          for (const value of ['0', '-1', '1.5', '1e3', '', '2147483648']) {
            assert.throws(() => core.quantityForSets(2, value));
          }
          assert.throws(() => core.quantityForSets(0, '100'));
          assert.throws(() => core.quantityForSets(2147483647, '2'));
          const input = [{key:'p:1',location_id:'2',quantity:'203'}];
          assert.deepEqual(core.submissionRows(input,'inbound'), [{key:'p:1',location_id:2,quantity:203}]);
          assert.deepEqual(core.submissionRows([{key:'p:1',location_id:'2',quantity:'0'}],'adjust'), [{key:'p:1',location_id:2,quantity:0}]);
          assert.throws(() => core.submissionRows([{key:'p:1',location_id:'2',quantity:'0'}],'inbound'));
          assert.throws(() => core.submissionRows([],'inbound'));
        '''], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inventory_and_order_entries_preserve_customer_and_order_filters(self):
        class Links(HTMLParser):
            def __init__(self, html):
                super().__init__()
                self.hrefs = []
                self.feed(html)
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.hrefs.append(dict(attrs).get('href', ''))
        inventory = self.client.get('/admin/inventory', query_string={'customer':'客户甲'}).get_data(as_text=True)
        batch_links = [urlsplit(href) for href in Links(inventory).hrefs if '/inventory/batch/inbound' in href]
        self.assertTrue(any(parse_qs(link.query).get('customer') == ['客户甲'] for link in batch_links))
        orders = self.client.get('/admin/orders').get_data(as_text=True)
        batch_links = [urlsplit(href) for href in Links(orders).hrefs if '/inventory/batch/inbound' in href]
        self.assertTrue(any(parse_qs(link.query).get('order_no') == ['SO-100'] and parse_qs(link.query).get('customer') == ['客户甲'] for link in batch_links))

    def test_large_result_keeps_filter_form_accessible(self):
        with app.get_db() as c:
            c.executemany("""INSERT INTO manuals (product_name,model,category,version,filename,original_filename,created_at,updated_at)
                VALUES (?,'','','','','','2026-09-08','2026-09-08')""", [(f'大量产品{i}',) for i in range(501)])
        for mode in ['inbound','adjust']:
            response = self.client.get('/admin/inventory/batch/' + mode)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('500', html)
            self.assertIn('id="batch-query"', html)
        self.assertEqual(len(self.page(customer='客户甲')['rows']), 2)

    def test_inventory_transaction_numbers_cross_thousand_boundary(self):
        prefix = 'INV' + datetime.now().strftime('%Y%m%d')
        with app.get_db() as c:
            c.execute("""INSERT INTO inventory_transactions (transaction_no,type,manual_id,quantity,created_at)
                VALUES (?,'in',?,1,'2026-09-08')""", (prefix+'999',self.ids[0]))
        response = self.post(self.payload(self.page(customer='客户甲'), [2,1]))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with app.get_db() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT transaction_no FROM inventory_transactions ORDER BY id')], [prefix+'999',prefix+'1000',prefix+'1001'])

    def test_failure_after_first_write_rolls_back_balances_orders_and_receipt(self):
        payload = self.payload(self.page(customer='客户甲', order_no='SO-100'), [2,1])
        original = app.create_inventory_transaction
        def write_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError('模拟写入后失败')
        with patch.object(app, 'create_inventory_transaction', side_effect=write_then_fail):
            self.assertEqual(self.post(payload).status_code, 400)
        self.assertEqual(self.quantities(), [0,0,0])
        with app.get_db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM inventory_transactions').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM inventory_batch_receipts').fetchone()[0], 0)
            self.assertEqual([r[0] for r in c.execute('SELECT inventory_received_quantity FROM product_orders')], [0,0])

    def test_logged_in_without_inventory_permission_cannot_write_or_add_locations(self):
        page = self.page(customer='客户甲')
        with app.get_db() as c:
            c.execute("""INSERT INTO users (username,password_hash,role,active,can_manage_warehouse_inventory,created_at,updated_at)
                VALUES ('no-inventory','hash','operator',1,0,'2026-09-08','2026-09-08')""")
        with self.client.session_transaction() as s:
            s.update(admin_username='no-inventory',admin_role='operator')
        self.assertEqual(self.post(self.payload(page,[2,1])).status_code, 302)
        self.assertEqual(self.client.post('/admin/inventory/locations/quick-create',json=dict(csrf_token=page['csrf_token'],name='禁止',code='DENY')).status_code,302)
        self.assertEqual(self.quantities(), [0,0,0])

    def test_stale_order_and_tampered_preview_are_rejected(self):
        page = self.page(customer='客户甲',order_no='SO-100')
        payload = self.payload(page,[2,1])
        with app.get_db() as c:
            c.execute('UPDATE product_orders SET inventory_received_quantity=1 WHERE id=?',(self.orders[0],))
        self.assertEqual(self.post(payload).status_code,409)
        payload['token'] = 'tampered.' + payload['token']
        self.assertEqual(self.post(payload).status_code,409)
        self.assertEqual(self.quantities(), [0,0,0])

    def test_expanded_location_limit_keeps_query_available(self):
        with app.get_db() as c:
            for i in range(251):
                product = c.execute("""INSERT INTO manuals (product_name,customer,model,category,version,filename,original_filename,created_at,updated_at)
                    VALUES (?,'多库位客户','','','','','','2026-09-08','2026-09-08')""", (f'多库位{i}',)).lastrowid
                c.executemany("INSERT INTO inventory_balances (manual_id,location_id,quantity,updated_at) VALUES (?,?,1,'2026-09-08')", [(product,self.loc),(product,self.loc2)])
        response = self.client.get('/admin/inventory/batch/adjust',query_string={'customer':'多库位客户'})
        self.assertEqual(response.status_code,200)
        self.assertIn('500',response.get_data(as_text=True))
        self.assertEqual(len(self.page('adjust',customer='多库位客户',location_id=self.loc)['rows']),251)

    def test_large_saved_batch_ledger_shows_all_rows(self):
        with app.get_db() as c:
            for i in range(301):
                c.execute("""INSERT INTO manuals (product_name,customer,model,category,version,filename,original_filename,created_at,updated_at)
                    VALUES (?,'大批客户','','','','','','2026-09-08','2026-09-08')""", (f'BATCH-{i:03d}',))
        page = self.page(customer='大批客户')
        response = self.post(self.payload(page,[1]*301))
        self.assertEqual(response.status_code,200)
        history = self.client.get(response.json['redirect_url']).get_data(as_text=True)
        self.assertTrue('BATCH-000' in history, '批次第一行未出现在流水页面')
        self.assertTrue('BATCH-300' in history, '批次最后一行未出现在流水页面')

    def test_each_row_can_choose_its_own_location(self):
        payload = self.payload(self.page(customer='客户甲'),[2,1])
        payload['rows'][1]['location_id'] = self.loc2
        self.assertEqual(self.post(payload).status_code,200)
        with app.get_db() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT manual_id,location_id,quantity FROM inventory_balances ORDER BY manual_id')],[(self.ids[0],self.loc,2),(self.ids[1],self.loc2,1)])

    def test_wrong_mode_expired_token_and_quick_create_csrf_are_rejected(self):
        page = self.page(customer='客户甲')
        payload = self.payload(page,[2,1])
        payload['remark'] = '盘点'
        self.assertEqual(self.post(payload,'adjust').status_code,403)
        serializer = app.URLSafeTimedSerializer(app.app.secret_key,salt='inventory-batch-v1')
        snapshot = serializer.loads(payload['token'])
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp',return_value=1000):
            payload['token'] = serializer.dumps(snapshot)
        self.assertEqual(self.post(payload).status_code,409)
        self.assertEqual(self.client.post('/admin/inventory/locations/quick-create',json=dict(csrf_token='wrong',code='D01',name='测试')).status_code,403)
        self.assertEqual(self.quantities(),[0,0,0])
