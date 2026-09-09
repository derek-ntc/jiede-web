"""Supplemental source snapshots and real ordinary-shipment workflow contracts."""

import sqlite3
import json
import re
import subprocess
from pathlib import Path
from io import BytesIO

import app
import shipping_workflow as shipping
from tests import test_delivery_note_items as delivery_tests
from tests.test_assembly_shipping import AssemblyTask7TestCase, VALID_PNG_BYTES


def insert_supplemental(conn, manual_id, *, price=113, currency='USD', quantity=2, token='supplemental'):
    operation_id = shipping.start_delivery_operation(conn, token, token, 'shipper')
    sid = conn.execute('''INSERT INTO supplemental_shipments
        (operation_id,manual_id,customer,drawing_no,product_name,specification_snapshot,
         sku,model,unit,shipped_quantity,inventory_deducted_quantity,inventory_shortage_quantity,
         shipped_at,logistics_no,remark,unit_price_minor,currency,price_recorded_by,price_recorded_at,
         created_by,created_at,updated_at)
        VALUES (?,?,'客户A','SUP-100','补充产品','冻结规格','SUP-SKU','SUP-MODEL','件',?,1,?,
                '2026-09-09','物流快照','行备注',?,?,'shipper','2026-09-09','shipper','2026-09-09','2026-09-09')''',
        (operation_id, manual_id, quantity, max(0, quantity - 1), price, currency)).lastrowid
    return operation_id, sid


class SupplementalShipmentTests(delivery_tests.DeliveryPersistenceFixture):
    def test_supplemental_loaders_use_snapshots_even_after_master_edit_or_removal(self):
        with app.get_db() as conn:
            operation_id, sid = insert_supplemental(conn, self.manual_a)
            note_id = shipping.create_delivery_notes(conn, operation_id, [('supplemental', sid)], {}, 'shipper')[0]
            conn.execute("UPDATE manuals SET drawing_no='NEW', product_name='新名', supplier='新规格', unit_price_minor=999, currency='CNY' WHERE id=?", (self.manual_a,))
            conn.execute('DELETE FROM manuals WHERE id=?', (self.manual_a,))
            item = shipping.load_delivery_note(conn, note_id)['items'][0]
            self.assertEqual((item['drawing_no'], item['specification'], item['shipped_quantity']), ('SUP-100', '冻结规格', 2))
            finance = app._fetch_finance_source(conn, 'supplemental', sid)
            self.assertEqual((finance['unit_price_minor'], finance['currency'], finance['line_total_minor']), (113, 'USD', 226))
            reconciliation = app._fetch_reconciliation_source(conn, 'supplemental', sid)
            self.assertEqual((reconciliation['sku'], reconciliation['model'], reconciliation['unit'], reconciliation['remark']),
                             ('SUP-SKU', 'SUP-MODEL', '件', '行备注'))
            self.assertEqual(reconciliation['order_no'], '未关联订单')
            self.assertEqual(reconciliation['delivery_no'], f'BC-{sid}')

    def test_supplemental_requires_positive_quantity_and_balanced_nonnegative_inventory(self):
        with app.get_db() as conn:
            _, sid = insert_supplemental(conn, self.manual_a)
            for assignment in ('shipped_quantity=0', 'shipped_quantity=-1',
                               'inventory_deducted_quantity=-1', 'inventory_shortage_quantity=-1',
                               'inventory_deducted_quantity=3', "remark='" + 'x' * 501 + "'"):
                with self.subTest(assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'UPDATE supplemental_shipments SET {assignment} WHERE id=?', (sid,))

    def test_supplemental_update_delete_trigger_preserves_snapshot_and_invalidates(self):
        with app.get_db() as conn:
            operation_id, sid = insert_supplemental(conn, self.manual_a)
            note_id = shipping.create_delivery_notes(conn, operation_id, [('supplemental', sid)], {}, 'shipper')[0]
            conn.execute("UPDATE delivery_notes SET updated_at='old'")
            conn.execute("UPDATE supplemental_shipments SET remark='changed' WHERE id=?", (sid,))
            note = shipping.load_delivery_note(conn, note_id)
            self.assertNotEqual(note['updated_at'], 'old')
            self.assertEqual(note['items'][0]['remark'], '行备注')
            conn.execute('DELETE FROM supplemental_shipments WHERE id=?', (sid,))
            note = shipping.load_delivery_note(conn, note_id)
            self.assertEqual(note['invalidated'], 1)
            self.assertEqual(note['items'][0]['remark'], '行备注')

    def test_supplemental_finance_and_reconciliation_claims_lock_source(self):
        with app.get_db() as conn:
            _, sid = insert_supplemental(conn, self.manual_a)
            refs = [('supplemental', sid)]
            invoice_id = app.create_finance_invoice(conn, self.customer_a, refs, 'finance')
            statement = app.create_reconciliation_statement(conn, refs, 'finance')
            self.assertTrue(app.finance_source_is_claimed(conn, 'supplemental', sid))
            self.assertTrue(app.reconciliation_source_is_claimed(conn, 'supplemental', sid))
            with self.assertRaises(ValueError):
                app.assert_finance_sources_mutable(conn, refs)
            with self.assertRaises(ValueError):
                app.assert_reconciliation_sources_mutable(conn, refs)
            self.assertEqual(conn.execute('SELECT total_minor FROM finance_invoices WHERE id=?', (invoice_id,)).fetchone()[0], 226)
            self.assertEqual(conn.execute('SELECT amount_incl_tax_minor FROM reconciliation_statements WHERE id=?', (statement['statement_id'],)).fetchone()[0], 226)


class OrderShipmentLineTests(AssemblyTask7TestCase):
    def setUp(self):
        super().setUp()
        self.product = self.create_product('ADDED')
        self.zero = self.create_product('ZERO')
        self.other = self.create_product('OTHER', '客户B')
        self.late = self.create_order(self.product, 'LATE', 3, planned_ship_at='2026-10-01')
        self.early = self.create_order(self.product, 'EARLY', 2, planned_ship_at='2026-09-01', ordered_at='2026-08-20')
        self.tie = self.create_order(self.product, 'TIE', 4, planned_ship_at='2026-09-01', ordered_at='2026-01-01')
        self.stock_product(self.product, 4)
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier='规格-S', unit='件', sku='SKU', model='MODEL', unit_price_minor=123, currency='USD'")
            self.customer = conn.execute("INSERT INTO customers(name,created_at,updated_at) VALUES ('客户A','','')").lastrowid
            conn.execute("INSERT INTO customers(name,created_at,updated_at) VALUES ('客户B','','')")

    def line(self, product=None, quantity=1, **extra):
        return dict(manual_id=product or self.product, customer='客户A',
                    source_kind='extra', quantity=quantity, remark='', **extra)

    def preview(self, lines):
        return self.client.post('/admin/shipped-orders/order-preview', json={'lines': lines})

    def save(self, lines, token='order-lines', **extra):
        preview = self.preview(lines)
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        return self.client.post('/admin/shipped-orders/new', data={
            'shipment_lines': json.dumps(lines), 'shipped_at': '2026-09-09',
            'logistics_no': '整车物流', 'operation_token': token,
            'preview_token': preview.get_json()['preview_token'], **extra})

    def supplemental_post(self, path, data=None):
        self.client.get('/admin/shipped-orders')
        with self.client.session_transaction() as session:
            csrf = session['shipment_price_csrf_token']
        return self.client.post(path, data={'csrf_token': csrf, **(data or {})})

    def test_added_product_allocates_delivery_date_then_id_and_supplemental_remainder(self):
        lines = [self.line(quantity=11), self.line(self.zero, 0)]
        lines[0]['remark'] = '<两箱> & 小心'
        lines[1]['remark'] = '本次不发'
        first = self.save(lines)
        self.assertRegex(first.location, r'/admin/delivery-notes/operations/\d+$')
        with app.get_db() as conn:
            ordinary = conn.execute('SELECT order_id, shipped_quantity, remark, logistics_no FROM product_order_shipments ORDER BY id').fetchall()
            self.assertEqual([tuple(r) for r in ordinary], [(self.early, 2, '<两箱> & 小心', '整车物流'), (self.tie, 4, '<两箱> & 小心', '整车物流'), (self.late, 3, '<两箱> & 小心', '整车物流')])
            sup = dict(conn.execute('SELECT * FROM supplemental_shipments').fetchone())
            self.assertEqual((sup['shipped_quantity'], sup['inventory_deducted_quantity'], sup['inventory_shortage_quantity']), (2, 0, 2))
            self.assertEqual((sup['unit_price_minor'], sup['currency'], sup['specification_snapshot'], sup['sku'], sup['model']), (123, 'USD', '规格-S', 'SKU', 'MODEL'))
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 0)
            self.assertEqual(conn.execute("SELECT SUM(quantity) FROM inventory_transactions WHERE type='out'").fetchone()[0], 4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM inventory_transactions WHERE manual_id=?', (self.zero,)).fetchone()[0], 0)
            note = shipping.load_delivery_note(conn, conn.execute('SELECT id FROM delivery_notes').fetchone()[0])
            self.assertEqual([(i['manual_id'], i['quantity'], i['remark']) for i in note['items']], [(self.product, 11, '<两箱> & 小心'), (self.zero, 0, '本次不发')])
            self.assertEqual(len(note['sources']), 4)
            refs = app.fetch_available_finance_sources(conn, '客户A')
            self.assertEqual(sum(r['quantity'] for r in refs), 11)
            invoice = app.create_finance_invoice(conn, self.customer, [('supplemental', sup['id'])], 'admin')
            self.assertEqual(conn.execute('SELECT total_minor FROM finance_invoices WHERE id=?', (invoice,)).fetchone()[0], 246)
            statement = app.create_reconciliation_statement(conn, [('supplemental', sup['id'])], 'admin')
            self.assertEqual(conn.execute('SELECT amount_incl_tax_minor FROM reconciliation_statements WHERE id=?', (statement['statement_id'],)).fetchone()[0], 246)
        page = self.client.get(first.location).get_data(as_text=True)
        self.assertIn('&lt;两箱&gt; &amp; 小心', page)
        self.assertIn('本次不发', page)

    def test_legacy_rows_allow_zero_remark_and_no_mutations(self):
        zero_order = self.create_order(self.zero, 'ZERO-ORDER', 7)
        response = self.client.post('/admin/shipped-orders/new', data={
            'order_id': [str(zero_order), str(self.early)], 'shipped_quantity': ['0', '1'],
            'line_remark': ['稍后补发', '先发'], 'shipped_at': '2026-09-09', 'logistics_no': '物流'})
        self.assertRegex(response.location, r'/admin/delivery-notes/operations/\d+$')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_order_shipments').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_orders WHERE id=?', (zero_order,)).fetchone()[0], 0)
            items = conn.execute('SELECT quantity,remark FROM delivery_note_items ORDER BY sort_order').fetchall()
            self.assertEqual([tuple(r) for r in items], [(0, '稍后补发'), (1, '先发')])

    def test_search_requires_customer_is_scoped_and_never_returns_prices(self):
        self.assertEqual(self.client.get('/admin/shipped-orders/order-product-options').status_code, 400)
        response = self.client.get('/admin/shipped-orders/order-product-options?customer=客户A&q=ADD')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['manual_id'] for r in response.get_json()['items']], [self.product])
        self.assertNotIn('unit_price', response.get_data(as_text=True))
        self.assertNotIn('USD', response.get_data(as_text=True))

    def test_validation_rejects_all_zero_invalid_quantity_forged_ownership_and_remarks(self):
        invalid = [[self.line(quantity=0)], [self.line(quantity=-1)], [self.line(quantity=True)],
                   [self.line(quantity=1.5)], [self.line(quantity=2147483648)],
                   [self.line(self.other)], [self.line(order_id=self.early + 9999)],
                   [{**self.line(), 'customer': ''}], [{**self.line(), 'remark': 'x' * 501}]]
        for lines in invalid:
            with self.subTest(lines=lines):
                self.assertEqual(self.preview(lines).status_code, 400)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 0)

    def test_stale_order_stock_and_customer_reject_atomically_with_fresh_preview(self):
        lines = [self.line(quantity=10)]
        preview = self.preview(lines)
        self.assertEqual(preview.status_code, 200)
        with app.get_db() as conn:
            conn.execute('UPDATE inventory_balances SET quantity=1 WHERE manual_id=?', (self.product,))
        response = self.client.post('/admin/shipped-orders/new', data={
            'shipment_lines': json.dumps(lines), 'shipped_at': '2026-09-09',
            'preview_token': preview.get_json()['preview_token'], 'operation_token': 'stale'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['preview']['items'][0]['inventory_deducted_quantity'], 1)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 0)

    def test_replay_preserves_sources_and_changed_remark_conflicts(self):
        lines = [self.line(self.zero, 2)]
        preview = self.preview(lines)
        self.assertEqual(preview.status_code, 200)
        data = dict(shipment_lines=json.dumps(lines), shipped_at='2026-09-09', operation_token='replay', preview_token=preview.get_json()['preview_token'])
        first = self.client.post('/admin/shipped-orders/new', data=data)
        second = self.client.post('/admin/shipped-orders/new', data=data)
        self.assertEqual(first.location, second.location)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 1)
        lines[0]['remark'] = 'changed'
        data['shipment_lines'] = json.dumps(lines)
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=data).status_code, 409)

    def test_supplemental_edit_delete_restores_only_actual_stock_and_keeps_price_snapshot(self):
        self.stock_product(self.zero, 3)
        self.save([self.line(self.zero, 5)])
        with app.get_db() as conn:
            sid = conn.execute('SELECT id FROM supplemental_shipments').fetchone()[0]
            note_id = conn.execute('SELECT id FROM delivery_notes').fetchone()[0]
            conn.execute('UPDATE manuals SET unit_price_minor=999')
        response = self.supplemental_post(f'/admin/shipped-orders/supplemental/{sid}/edit', data={
            'shipped_quantity': '2', 'shipped_at': '2026-09-10', 'remark': '新备注', 'logistics_no': '新物流'})
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM supplemental_shipments WHERE id=?', (sid,)).fetchone()
            self.assertEqual((row['shipped_quantity'], row['inventory_deducted_quantity'], row['inventory_shortage_quantity'], row['unit_price_minor']), (2, 2, 0, 123))
            self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 1)
            self.assertEqual(shipping.load_delivery_note(conn, note_id)['items'][0]['quantity'], 5)
        self.assertEqual(self.supplemental_post(f'/admin/shipped-orders/supplemental/{sid}/delete').status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 3)
            self.assertEqual(shipping.load_delivery_note(conn, note_id)['invalidated'], 1)

    def test_supplemental_finance_or_reconciliation_locks_edits_and_delete(self):
        self.save([self.line(self.zero, 2)])
        for kind in ('finance', 'reconciliation'):
            with self.subTest(kind=kind), app.get_db() as conn:
                sid = conn.execute('SELECT id FROM supplemental_shipments').fetchone()[0]
                refs = [('supplemental', sid)]
                if kind == 'finance':
                    claim = app.create_finance_invoice(conn, self.customer, refs, 'admin')
                else:
                    claim = app.create_reconciliation_statement(conn, refs, 'admin')['statement_id']
            self.assertEqual(self.supplemental_post(f'/admin/shipped-orders/supplemental/{sid}/edit', data={'shipped_quantity': '1', 'shipped_at': '2026-09-09', 'remark': 'new'}).status_code, 302)
            self.assertEqual(self.supplemental_post(f'/admin/shipped-orders/supplemental/{sid}/delete').status_code, 302)
            self.assertEqual(self.supplemental_post(f'/admin/shipped-orders/supplemental/{sid}/edit', data={'shipped_quantity': '2', 'shipped_at': '2026-09-09', 'remark': '', 'logistics_no': '已锁定仍可更新物流'}).status_code, 302)
            with app.get_db() as conn:
                self.assertEqual(conn.execute('SELECT shipped_quantity FROM supplemental_shipments WHERE id=?', (sid,)).fetchone()[0], 2)
                self.assertEqual(conn.execute('SELECT logistics_no FROM supplemental_shipments WHERE id=?', (sid,)).fetchone()[0], '已锁定仍可更新物流')
                if kind == 'finance':
                    app.delete_pending_finance_invoice(conn, claim)
                else:
                    app.void_reconciliation_statement(conn, claim, 'test', 'admin')

    def test_zero_only_customer_group_has_note_but_no_source(self):
        lines = [self.line(quantity=1), {**self.line(self.other, 0), 'customer': '客户B', 'remark': '客户B暂不发'}]
        response = self.save(lines)
        self.assertRegex(response.location, r'/admin/delivery-notes/operations/\d+$')
        with app.get_db() as conn:
            notes = [shipping.load_delivery_note(conn, r[0]) for r in conn.execute('SELECT id FROM delivery_notes ORDER BY customer')]
            self.assertEqual([(n['customer'], len(n['sources']), n['invalidated']) for n in notes], [('客户A', 1, 0), ('客户B', 0, 0)])

    def test_snapshot_failure_rolls_back_supplemental_inventory_and_operations(self):
        self.stock_product(self.zero, 2)
        with app.get_db() as conn:
            conn.execute("CREATE TRIGGER reject_task6_item BEFORE INSERT ON delivery_note_items BEGIN SELECT RAISE(ABORT, 'task6 failure'); END")
        response = self.save([self.line(self.zero, 3)])
        self.assertEqual(response.status_code, 500)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 0)
            self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 2)

    def test_supplemental_list_filters_and_reconciliation_selection_are_visible(self):
        self.save([self.line(self.zero, 2)])
        page = self.client.get('/admin/shipped-orders?customer=客户A&q=ZERO').get_data(as_text=True)
        self.assertIn('补充发货', page)
        self.assertIn('value="supplemental:1"', page)
        self.assertIn('/admin/shipped-orders/supplemental/1/edit', page)
        self.assertNotIn('/admin/shipped-orders/supplemental/1/edit', self.client.get('/admin/shipped-orders?customer=客户B').get_data(as_text=True))

    def test_order_browser_add_merge_zero_remark_and_stale_customer_search(self):
        result = subprocess.run(['node', 'tests/order_shipping_ui.cjs'], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_order_browser_refreshes_stale_specification_without_losing_edits_or_ime(self):
        result = subprocess.run(['node', 'tests/order_shipping_ui.cjs', 'stale-spec'], cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supplemental_only_images_are_saved_and_invalid_image_rolls_back(self):
        response = self.save([self.line(self.zero, 2)], token='invalid-photo', images=(BytesIO(b'not-png'), 'bad.png'))
        self.assertEqual(response.location, '/admin/shipped-orders/create')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)
        first = self.save([self.line(self.zero, 2)], images=(BytesIO(VALID_PNG_BYTES), 'photo.png'))
        self.assertRegex(first.location, r'/admin/delivery-notes/operations/\d+$')
        self.assertEqual(len(list(app.SHIPMENT_IMAGES_DIR.iterdir())), 1)
        with app.get_db() as conn:
            image = conn.execute('SELECT * FROM supplemental_shipment_images').fetchone()
            self.assertEqual(image['original_filename'], 'photo.png')
        self.assertEqual(self.client.get('/shipment-image/'+image['filename']).status_code, 200)
        self.supplemental_post('/admin/shipped-orders/supplemental/1/delete')
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_zero_only_customer_note_survives_master_edit_but_operation_source_delete_invalidates(self):
        self.save([self.line(quantity=1), {**self.line(self.other, 0), 'customer': '客户B'}])
        with app.get_db() as conn:
            note_id = conn.execute("SELECT id FROM delivery_notes WHERE customer='客户B'").fetchone()[0]
            conn.execute("UPDATE manuals SET product_name='CHANGED',supplier='CHANGED' WHERE id=?", (self.other,))
            note = shipping.load_delivery_note(conn, note_id)
            self.assertEqual(note['invalidated'], 0)
            self.assertEqual(note['items'][0]['specification'], '规格-S')
            conn.execute('DELETE FROM product_order_shipments')
            self.assertEqual(shipping.load_delivery_note(conn, note_id)['invalidated'], 1)

    def test_no_price_permission_has_no_price_in_order_form_notes_or_supplemental_edit(self):
        response = self.save([self.line(self.zero, 2)])
        with app.get_db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,role,can_manage_shipped,can_view_shipped,can_view_prices,created_at,updated_at) VALUES ('no-prices','hash','operator',1,1,0,'','')")
        with self.client.session_transaction() as session:
            session['admin_username']='no-prices'
            session['admin_role']='operator'
        for url in ('/admin/shipped-orders/create', '/admin/shipped-orders', response.location, '/admin/shipped-orders/supplemental/1/edit'):
            page=self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('1.23', page.get_data(as_text=True))
            self.assertNotIn('USD', page.get_data(as_text=True))
        preview=self.preview([self.line(self.zero, 3)])
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn('unit_price', preview.get_data(as_text=True))

    def test_ordinary_shortage_can_be_edited_and_deleted_without_inventing_stock(self):
        response = self.save([self.line(quantity=6)])
        with app.get_db() as conn:
            sid = conn.execute('SELECT id FROM product_order_shipments ORDER BY id DESC LIMIT 1').fetchone()[0]
        response = self.client.post(f'/admin/shipped-orders/{sid}/edit', data={'shipped_quantity': '3', 'shipped_at': '2026-09-10', 'logistics_no': '调整'})
        self.assertEqual(response.location, '/admin/shipped-orders')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_order_shipments WHERE id=?', (sid,)).fetchone()[0], 3)
            self.assertEqual(app.shipment_inventory_deducted_quantity(conn, sid), 2)
        self.client.post(f'/admin/shipped-orders/{sid}/delete')
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 2)

    def test_preview_rejects_new_ownership_and_order_balance_at_save(self):
        lines = [self.line(quantity=3)]
        preview = self.preview(lines).get_json()
        with app.get_db() as conn:
            conn.execute('UPDATE product_orders SET shipped_quantity=2 WHERE id=?', (self.early,))
        data = {'shipment_lines':json.dumps(lines), 'shipped_at':'2026-09-09','preview_token':preview['preview_token'],'operation_token':'changed-order'}
        changed = self.client.post('/admin/shipped-orders/new', data=data)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.get_json()['preview']['items'][0]['allocations'][0]['order_id'], self.tie)
        data['preview_token']=changed.get_json()['preview']['preview_token']
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET customer='客户B' WHERE id=?", (self.product,))
        rejected = self.client.post('/admin/shipped-orders/new',data=data)
        self.assertEqual(rejected.location, '/admin/shipped-orders/create')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_order_shipments').fetchone()[0], 0)

    def test_ordinary_metadata_edit_preserves_shortage_after_replenishment(self):
        order_id = self.create_order(self.zero, 'SHORTAGE', 10)
        self.stock_product(self.zero, 2)
        response = self.client.post('/admin/shipped-orders/new', data={
            'order_id': str(order_id), 'shipped_quantity': '5', 'shipped_at': '2026-09-09'})
        self.assertRegex(response.location, r'/admin/delivery-notes/operations/\d+$')
        self.stock_product(self.zero, 3)
        with app.get_db() as conn:
            sid = conn.execute('SELECT id FROM product_order_shipments').fetchone()[0]
            transactions = [tuple(row) for row in conn.execute("SELECT * FROM inventory_transactions WHERE type='out' ORDER BY id")]
            self.assertEqual(app.shipment_inventory_deducted_quantity(conn, sid), 2)
        for date, note in [('2026-09-09', '只改物流'), ('2026-09-10', '再改日期')]:
            with self.subTest(date=date):
                response = self.client.post(f'/admin/shipped-orders/{sid}/edit', data={
                    'shipped_quantity': '5', 'shipped_at': date, 'logistics_no': note})
                self.assertEqual(response.location, '/admin/shipped-orders')
                with app.get_db() as conn:
                    source = conn.execute('SELECT * FROM product_order_shipments WHERE id=?', (sid,)).fetchone()
                    self.assertEqual((source['shipped_at'], source['logistics_no']), (date, note))
                    deducted = app.shipment_inventory_deducted_quantity(conn, sid)
                    self.assertEqual((deducted, source['shipped_quantity'] - deducted), (2, 3))
                    self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 3)
                    self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM inventory_transactions WHERE type='out' ORDER BY id")], transactions)

    def test_preview_rejects_changed_allocation_inputs_even_when_allocation_is_unchanged(self):
        lines = [self.line(quantity=1)]
        for name, statement, values in [
            ('balance', 'UPDATE product_orders SET shipped_quantity=1 WHERE id=?', (self.early,)),
            ('later-balance', 'UPDATE product_orders SET quantity=8 WHERE id=?', (self.late,)),
            ('date', 'UPDATE product_orders SET planned_ship_at=? WHERE id=?', ('2026-08-31', self.early)),
        ]:
            with self.subTest(change=name):
                before = self.preview(lines).get_json()
                with app.get_db() as conn:
                    conn.execute(statement, values)
                after = self.preview(lines).get_json()
                self.assertEqual(before['items'][0]['allocations'], after['items'][0]['allocations'])
                response = self.client.post('/admin/shipped-orders/new', data={
                    'shipment_lines': json.dumps(lines), 'shipped_at': '2026-09-09',
                    'preview_token': before['preview_token'], 'operation_token': 'changed-' + name})
                self.assertEqual(response.status_code, 409)
                self.assertNotEqual(before['preview_token'], after['preview_token'])
                self.assertEqual(response.get_json()['preview']['preview_token'], after['preview_token'])
                with app.get_db() as conn:
                    for table in ('delivery_operations', 'product_order_shipments', 'supplemental_shipments'):
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
                    self.assertEqual(app.inventory_total_for_manual(conn, self.product), 4)

    def test_supplemental_mutations_require_session_csrf_and_templates_supply_it(self):
        self.stock_product(self.zero, 2)
        self.save([self.line(self.zero, 3)])
        edit_url = '/admin/shipped-orders/supplemental/1/edit'
        delete_url = '/admin/shipped-orders/supplemental/1/delete'
        data = {'shipped_quantity': '1', 'shipped_at': '2026-09-10', 'remark': '修改'}
        edit_page = self.client.get(edit_url).get_data(as_text=True)
        token_match = re.search(r'name="csrf_token" value="([^"]+)"', edit_page)
        self.assertIsNotNone(token_match)
        csrf = token_match.group(1)
        with self.client.session_transaction() as session:
            self.assertEqual(csrf, session['shipment_price_csrf_token'])
        for url in (edit_url, delete_url):
            for supplied in ({}, {'csrf_token': 'invalid'}):
                with self.subTest(url=url, supplied=supplied):
                    self.assertEqual(self.client.post(url, data={**data, **supplied}).status_code, 403)
        page = self.client.get('/admin/shipped-orders').get_data(as_text=True)
        delete_form = re.search(r'<form[^>]+action="' + delete_url + r'".*?</form>', page, re.S)
        self.assertIsNotNone(delete_form)
        self.assertIn(f'name="csrf_token" value="{csrf}"', delete_form.group(0))
        with app.get_db() as conn:
            self.assertEqual(tuple(conn.execute('SELECT shipped_quantity,inventory_deducted_quantity FROM supplemental_shipments').fetchone()), (3, 2))
            self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 0)
        self.assertEqual(self.client.post(edit_url, data={**data, 'csrf_token': csrf}).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM supplemental_shipments').fetchone()[0], 1)
        self.assertEqual(self.client.post(delete_url, data={'csrf_token': csrf}).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)
            self.assertEqual(app.inventory_total_for_manual(conn, self.zero), 2)

    def test_supplemental_csrf_does_not_grant_shipment_permission(self):
        self.save([self.line(self.zero, 2)])
        self.client.get('/admin/shipped-orders')
        with app.get_db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,role,can_manage_shipped,can_view_shipped,created_at,updated_at) VALUES ('view-only','hash','operator',0,1,'','')")
        with self.client.session_transaction() as session:
            csrf = session['shipment_price_csrf_token']
            session['admin_username'] = 'view-only'
            session['admin_role'] = 'operator'
        for action in ('edit', 'delete'):
            response = self.client.post(f'/admin/shipped-orders/supplemental/1/{action}', data={
                'csrf_token': csrf, 'shipped_quantity': '1', 'shipped_at': '2026-09-10'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, '/admin')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM supplemental_shipments').fetchone()[0], 2)
