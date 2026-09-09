import json
import re
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app
from tests.test_assembly_shipping import AssemblyTask7TestCase, VALID_PNG_BYTES


class FlexibleAssemblyShippingTests(AssemblyTask7TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(app, **{
            name: Path(self.files.name) / name.lower()
            for name in (
                'DATA_DIR', 'MANUALS_DIR', 'SIGNATURES_DIR',
                'RECONCILIATION_SIGNATURES_DIR', 'SHIPMENT_IMAGES_DIR',
                'INSPECTION_REPORTS_DIR', 'PRODUCTION_DRAWINGS_DIR',
            )
        })
        self.paths.start()
        super().setUp()
        self.p1 = self.create_product('P1')
        self.p2 = self.create_product('P2')
        self.p3 = self.create_product('P3')
        self.foreign = self.create_product('FOREIGN', '客户B')
        self.configure_components([(self.p1, 2), (self.p2, 3)])
        self.o1 = self.create_order(self.p1, 'SO-1', 50)
        self.o2 = self.create_order(self.p2, 'SO-2', 50)
        for product, stock in [(self.p1, 40), (self.p2, 40), (self.p3, 4)]:
            self.stock_product(product, stock)
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier='规格一', unit_price_minor=0 WHERE id=?", (self.p1,))
            conn.execute("UPDATE manuals SET supplier='规格三', unit_price_minor=123 WHERE id=?", (self.p3,))

    def tearDown(self):
        super().tearDown()
        self.paths.stop()
        self.files.cleanup()

    def flexible_preview(self, ids=None, quantities=None, batch_id=None, remarks=None):
        ids = [self.p1, self.p3] if ids is None else ids
        quantities = {self.p1: 20, self.p3: 7} if quantities is None else quantities
        return self.client.post(
            f'/admin/shipped-orders/assembly/{batch_id}/edit' if batch_id else
            '/admin/shipped-orders/assembly-preview',
            json={'customer': '客户A', 'assembly_drawing_no': 'ASM-100',
                  'set_quantity': 10, 'selected_manual_ids': ids, 'overrides': quantities,
                  'remarks': remarks},
        )

    def save_flexible(self, preview, batch_id=None, **extra):
        data = self.save_data(preview, extra_data=extra)
        data['selected_manual_ids'] = [str(item['manual_id']) for item in preview['items']]
        data.setdefault('line_remark', [item.get('remark', '') for item in preview['items']])
        return self.client.post(
            f'/admin/shipped-orders/assembly/{batch_id}/edit' if batch_id else
            '/admin/shipped-orders/assembly/new', data=data,
        )

    def test_zero_bom_row_keeps_remark_but_has_no_business_quantity(self):
        response = self.flexible_preview([self.p2, self.p1], {self.p2: 0, self.p1: 3},
                                         remarks={self.p2: '本次不发 <待补>', self.p1: '先发三件'})
        self.assertEqual(response.status_code, 200, response.get_json())
        preview = response.get_json()
        zero = preview['items'][0]
        self.assertEqual((zero['shipped_quantity'], zero['calculated_quantity'], zero['remark']),
                         (0, 30, '本次不发 <待补>'))
        self.assertEqual(zero['allocations'], [])
        self.assertEqual((zero['inventory_deducted_quantity'], zero['inventory_shortage_quantity'], zero['no_order_quantity']), (0, 0, 0))
        self.assertFalse(any(w['manual_id'] == self.p2 for w in preview['warnings']))
        saved = self.save_flexible(preview)
        self.assertEqual(saved.status_code, 201, saved.get_json())
        with app.get_db() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM assembly_shipment_items ORDER BY id')]
            self.assertEqual([(r['manual_id'], r['shipped_quantity'], r['remark']) for r in rows],
                             [(self.p2, 0, '本次不发 <待补>'), (self.p1, 3, '先发三件')])
            zero_id = rows[0]['id']
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assembly_shipment_allocations WHERE item_id=?', (zero_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_transactions WHERE type='out' AND manual_id=?", (self.p2,)).fetchone()[0], 0)
            self.assertEqual(app.inventory_total_for_manual(conn, self.p2), 40)
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_orders WHERE id=?', (self.o2,)).fetchone()[0], 0)
            self.assertIsNone(app._fetch_finance_source(conn, 'assembly_item', zero_id))
            self.assertIsNone(app._fetch_reconciliation_source(conn, 'assembly_item', zero_id))
            sources = app.fetch_available_finance_sources(conn, '客户A')
            self.assertEqual([r['source_id'] for r in sources], [rows[1]['id']])

    def test_legacy_rows_gain_empty_remark_without_rewriting_existing_data(self):
        saved = self.save_flexible(self.flexible_preview().get_json())
        self.assertEqual(saved.status_code, 201, saved.get_json())
        with app.get_db() as conn:
            conn.execute('ALTER TABLE assembly_shipment_items DROP COLUMN remark')
            before = [dict(row) for row in conn.execute('SELECT * FROM assembly_shipment_items ORDER BY id')]
        app.init_db()
        app.init_db()
        with app.get_db() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM assembly_shipment_items ORDER BY id')]
            self.assertEqual([row.pop('remark') for row in rows], ['', ''])
            self.assertEqual(rows, before)

    def test_remark_length_validation_edit_and_idempotent_migration(self):
        for invalid in [{self.p1: '注' * 501}, {self.foreign: '错误客户'}, {self.p1: {'bad': 'type'}}]:
            response = self.flexible_preview(remarks=invalid)
            self.assertEqual(response.status_code, 400, response.get_json())
        preview = self.flexible_preview(remarks={self.p1: '注' * 500, self.p3: '临时配件'}).get_json()
        self.assertEqual(preview['items'][0].get('remark'), '注' * 500)
        saved = self.save_flexible(preview)
        self.assertEqual(saved.status_code, 201, saved.get_json())
        batch_id = saved.get_json()['batch_id']
        app.init_db()
        app.init_db()
        page = self.client.get(f'/admin/shipped-orders/assembly/{batch_id}/edit').get_data(as_text=True)
        self.assertIn('<th>本行备注</th>', page)
        initial = json.loads(re.search(r'data-assembly-initial-preview>(.*?)</script>', page, re.S)[1])
        self.assertEqual([item['remark'] for item in initial['items']], ['注' * 500, '临时配件'])
        changed = self.flexible_preview(batch_id=batch_id, remarks={self.p1: '', self.p3: '<script>明细</script>'}).get_json()
        self.assertEqual(self.save_flexible(changed, batch_id).status_code, 201)
        with app.get_db() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT remark FROM assembly_shipment_items ORDER BY id')], ['', '<script>明细</script>'])

    def test_all_zero_and_invalid_complete_lines_do_not_create_operation(self):
        response = self.flexible_preview([self.p1, self.p3], {self.p1: 0, self.p3: 0})
        self.assertEqual(response.status_code, 400)
        self.assertIn('至少有一个产品的发货数量必须大于 0', response.get_json()['error'])
        preview = self.flexible_preview().get_json()
        for extra in [dict(shipped_quantity=['0', '0']), dict(line_remark=['错位']),
                      dict(line_remark=['注' * 501, '']), dict(shipped_quantity=['2147483648', '7'])]:
            with self.subTest(extra=extra):
                result = self.save_flexible(preview, **extra)
                self.assertEqual(result.status_code, 400, result.get_json())
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assembly_shipment_items').fetchone()[0], 0)
            for item in preview['items']:
                item.update(shipped_quantity=0, allocations=[], inventory_deducted_quantity=0, inventory_shortage_quantity=0)
            with self.assertRaisesRegex(ValueError, '至少有一个产品'):
                app.save_assembly_shipment(conn, preview, '2026-09-09', '', 'admin')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assembly_shipment_batches').fetchone()[0], 0)

    def test_save_rechecks_decreased_stock_and_keeps_zero_extra_document_line(self):
        response = self.flexible_preview([self.p1, self.p3], {self.p1: 20, self.p3: 0})
        self.assertEqual(response.status_code, 200, response.get_json())
        preview = response.get_json()
        with app.get_db() as conn:
            conn.execute('UPDATE inventory_balances SET quantity=2 WHERE manual_id=?', (self.p1,))
        stale = self.save_flexible(preview)
        self.assertEqual(stale.status_code, 409, stale.get_json())
        refreshed = stale.get_json()['preview']
        self.assertEqual([(w['code'], w['quantity']) for w in refreshed['warnings']], [('inventory_shortage', 18)])
        self.assertEqual(self.save_flexible(refreshed, confirm_warnings='0').status_code, 409)
        self.assertEqual(self.save_flexible(refreshed).status_code, 201)
        with app.get_db() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT shipped_quantity, inventory_deducted_quantity, inventory_shortage_quantity FROM assembly_shipment_items ORDER BY id')], [(20, 2, 18), (0, 0, 0)])
            self.assertEqual(app.inventory_total_for_manual(conn, self.p3), 4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM supplemental_shipments').fetchone()[0], 0)

    def test_selected_components_only_allocate_and_deduct_without_changing_bom(self):
        with app.get_db() as conn:
            preview = app.build_assembly_shipment_preview(
                conn, '客户A', 'ASM-100', 10,
                overrides={self.p1: 20, self.p3: 7},
                selected_manual_ids=[self.p1, self.p3],
            )
        self.assertEqual([row['manual_id'] for row in preview['items']], [self.p1, self.p3])
        self.assertEqual(preview['items'][1]['source_kind'], 'extra')
        self.assertEqual(preview['items'][1]['shipped_quantity'], 7)
        self.assertEqual(preview['items'][1]['quantity_per_set'], 0)
        self.assertEqual(preview['items'][1]['calculated_quantity'], 0)
        self.assertEqual(preview['items'][1]['specification'], '规格三')
        self.assertEqual({w['code'] for w in preview['warnings']}, {'no_order', 'inventory_shortage'})
        result = self.save_flexible(preview)
        self.assertEqual(result.status_code, 201, result.get_json())
        with app.get_db() as conn:
            items = conn.execute('SELECT manual_id, source_kind FROM assembly_shipment_items ORDER BY id').fetchall()
            self.assertEqual([tuple(row) for row in items], [(self.p1, 'bom'), (self.p3, 'extra')])
            self.assertEqual([app.inventory_total_for_manual(conn, p) for p in [self.p1, self.p2, self.p3]], [20, 40, 0])
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_orders WHERE id=?', (self.o2,)).fetchone()[0], 0)
            self.assertEqual([row['manual_id'] for row in app.get_assembly_definition(conn, '客户A', 'ASM-100')], [self.p1, self.p2])

    def test_explicit_empty_duplicate_cross_customer_and_invalid_quantities_rejected(self):
        cases = [([], {}), ([self.p1, self.p1], {self.p1: 2}),
                 ([self.foreign], {self.foreign: 2}), ([999999], {999999: 2}),
                 ([self.p3], {}), ([self.p3], {self.p3: 0}),
                 ([self.p3], {self.p3: 1.5}), ([self.p3], {self.p3: -1})]
        for ids, quantities in cases:
            with self.subTest(ids=ids, quantities=quantities):
                response = self.flexible_preview(ids, quantities)
                self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.post_preview(sets=10).status_code, 200)

    def test_warning_confirmation_and_changed_specification_invalidate_token(self):
        response = self.flexible_preview()
        self.assertEqual(response.status_code, 200, response.get_json())
        preview = response.get_json()
        self.assertEqual(self.save_flexible(preview, confirm_warnings='0').status_code, 409)
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier='新版规格' WHERE id=?", (self.p3,))
        stale = self.save_flexible(preview)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()['preview']['items'][1]['specification'], '新版规格')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assembly_shipment_batches').fetchone()[0], 0)

    def test_edit_starts_from_history_even_if_bom_disappears_and_preserves_snapshots(self):
        response = self.flexible_preview()
        self.assertEqual(response.status_code, 200, response.get_json())
        saved = self.save_flexible(response.get_json())
        self.assertEqual(saved.status_code, 201, saved.get_json())
        batch_id = saved.get_json()['batch_id']
        with app.get_db() as conn:
            conn.execute('DELETE FROM product_assembly_components')
            conn.execute("UPDATE manuals SET supplier='新规格', product_name='新名称', unit_price_minor=999")
        page = self.client.get(f'/admin/shipped-orders/assembly/{batch_id}/edit')
        self.assertEqual(page.status_code, 200)
        initial = json.loads(re.search(r'data-assembly-initial-preview>(.*?)</script>', page.get_data(as_text=True), re.S)[1])
        self.assertEqual([row['manual_id'] for row in initial['items']], [self.p1, self.p3])
        self.assertEqual(initial['items'][0]['specification'], '规格一')
        self.assertNotEqual(initial['items'][0]['product_name'], '新名称')
        updated = self.flexible_preview([self.p1, self.p2], {self.p1: 10, self.p2: 5}, batch_id)
        self.assertEqual(updated.status_code, 200, updated.get_json())
        result = self.save_flexible(updated.get_json(), batch_id)
        self.assertEqual(result.status_code, 201, result.get_json())
        with app.get_db() as conn:
            rows = conn.execute('SELECT manual_id, source_kind, unit_price_minor, specification_snapshot FROM assembly_shipment_items ORDER BY id').fetchall()
            self.assertEqual([tuple(row) for row in rows], [(self.p1, 'bom', 0, '规格一'), (self.p2, 'extra', 999, '新规格')])
            self.assertEqual([app.inventory_total_for_manual(conn, p) for p in [self.p1, self.p2, self.p3]], [30, 35, 4])
        app.init_db()
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_order_shipments').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT shipped_quantity FROM product_orders WHERE id=?', (self.o1,)).fetchone()[0], 10)

    def test_browser_claimed_metadata_is_ignored_and_candidates_are_scoped_price_free(self):
        response = self.client.get('/admin/shipped-orders/component-options', query_string={'customer': '客户A', 'q': 'P3'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['items'], [{'manual_id': self.p3, 'drawing_no': 'P3', 'product_name': '产品-P3', 'specification': '规格三'}])
        response = self.client.post('/admin/shipped-orders/assembly-preview', json={
            'customer': '客户A', 'assembly_drawing_no': 'ASM-100', 'set_quantity': 10,
            'selected_manual_ids': [self.p3], 'overrides': {self.p3: 7},
            'items': [{'manual_id': self.p3, 'source_kind': 'bom', 'product_name': '伪造', 'specification': '伪造', 'quantity_per_set': 99}],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        item = response.get_json()['items'][0]
        self.assertEqual((item['source_kind'], item['specification'], item['quantity_per_set']), ('extra', '规格三', 0))
        self.assertNotIn('price', json.dumps(response.get_json()))
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/admin/shipped-orders/component-options?customer=客户A').status_code, 302)

    def test_image_failure_rolls_back_flexible_selection_stock_and_allocations(self):
        response = self.flexible_preview()
        self.assertEqual(response.status_code, 200, response.get_json())
        with patch.object(app, '_finalize_assembly_shipment_image', side_effect=OSError('disk failure')):
            failed = self.save_flexible(response.get_json(), images=(BytesIO(VALID_PNG_BYTES), 'fail.png', 'image/png'))
        self.assertEqual(failed.status_code, 500)
        with app.get_db() as conn:
            for table in ['assembly_shipment_batches', 'assembly_shipment_items', 'assembly_shipment_allocations']:
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual([app.inventory_total_for_manual(conn, p) for p in [self.p1, self.p2, self.p3]], [40, 40, 4])
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_edit_explicit_string_selection_without_overrides_retains_actual_quantities(self):
        initial = self.flexible_preview().get_json()
        batch_id = self.save_flexible(initial).get_json()['batch_id']
        response = self.client.post(f'/admin/shipped-orders/assembly/{batch_id}/edit', json={
            'selected_manual_ids': [str(self.p1), str(self.p3)],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual([item['shipped_quantity'] for item in response.get_json()['items']], [20, 7])

    def test_retained_null_and_empty_specs_and_extra_source_survive_bom_changes(self):
        initial = self.flexible_preview().get_json()
        batch_id = self.save_flexible(initial).get_json()['batch_id']
        self.configure_components([(self.p1, 99), (self.p3, 88)])
        with app.get_db() as conn:
            conn.execute('UPDATE assembly_shipment_items SET specification_snapshot=NULL WHERE manual_id=?', (self.p1,))
            conn.execute("UPDATE assembly_shipment_items SET specification_snapshot='' WHERE manual_id=?", (self.p3,))
            conn.execute("UPDATE manuals SET supplier='更新规格', unit_price_minor=999")
        preview = self.flexible_preview(batch_id=batch_id).get_json()
        self.assertEqual([(i['source_kind'], i['quantity_per_set'], i['specification']) for i in preview['items']], [('bom', 2, '更新规格'), ('extra', 0, '')])
        self.assertEqual(self.save_flexible(preview, batch_id).status_code, 201)
        with app.get_db() as conn:
            rows = conn.execute('SELECT specification_snapshot, unit_price_minor FROM assembly_shipment_items ORDER BY id').fetchall()
            self.assertEqual([tuple(row) for row in rows], [(None, 0), ('', 123)])

    def test_manage_without_price_permission_can_search_by_name_without_price_projection(self):
        with app.get_db() as conn:
            conn.execute("INSERT INTO users (username,password_hash,role,active,can_view_shipped,can_manage_shipped,created_at,updated_at) VALUES ('shipper','unused','operator',1,1,1,'now','now')")
        with self.client.session_transaction() as session:
            session['admin_username'] = 'shipper'
            session['admin_role'] = 'operator'
        response = self.client.get('/admin/shipped-orders/component-options', query_string={'customer': '客户A', 'q': '产品-'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['manual_id'] for item in response.get_json()['items']], [self.p1, self.p2, self.p3])
        for item in response.get_json()['items']:
            self.assertEqual(set(item), {'manual_id', 'drawing_no', 'product_name', 'specification'})

    def test_explicit_empty_or_mismatched_save_cannot_fall_back_to_full_bom(self):
        preview = self.post_preview(sets=10).get_json()
        for extra in [{'assembly_selection_explicit': '1'}, {'selected_manual_ids': [str(self.p1)]}, {'selected_manual_ids': [str(self.p1), str(self.p1)]}]:
            with self.subTest(extra=extra):
                response = self.client.post('/admin/shipped-orders/assembly/new', data=self.save_data(preview, extra_data=extra))
                self.assertEqual(response.status_code, 400)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM assembly_shipment_batches').fetchone()[0], 0)

    def test_reconciliation_lock_disables_adjustments_and_rejects_remove_add(self):
        initial = self.flexible_preview().get_json()
        batch_id = self.save_flexible(initial).get_json()['batch_id']
        with app.get_db() as conn:
            item_id = conn.execute('SELECT id FROM assembly_shipment_items WHERE manual_id=?', (self.p1,)).fetchone()[0]
            app.create_reconciliation_statement(conn, [('assembly_item', item_id)], 'admin')
            before = [tuple(row) for row in conn.execute('SELECT * FROM assembly_shipment_items ORDER BY id')]
        page = self.client.get(f'/admin/shipped-orders/assembly/{batch_id}/edit').get_data(as_text=True)
        self.assertIn('data-assembly-finance-claimed="1"', page)
        self.assertNotIn('data-assembly-component-search', page)
        changed = self.flexible_preview([self.p3], {self.p3: 7}, batch_id).get_json()
        self.assertEqual(self.save_flexible(changed, batch_id).status_code, 400)
        self.assertEqual(self.save_flexible(initial, batch_id, line_remark=['篡改锁定行', '']).status_code, 400)
        self.assertEqual(self.save_flexible(initial, batch_id, logistics_no='仍可维护备注').status_code, 201)
        with app.get_db() as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM assembly_shipment_items ORDER BY id')], before)

    def test_browser_selection_recalculation_empty_state_and_stale_candidate_race(self):
        script = r'''
        (async () => {
          const assert = require('node:assert/strict');
          const fs = require('node:fs');
          const vm = require('node:vm');
          class Element {
            constructor(value='') { this.value=value; this.dataset={}; this.children=[]; this.listeners={}; this.classList={toggle(){},add(){}}; }
            addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
            async emit(type, event={target:this, preventDefault(){}}) { for (const fn of this.listeners[type] || []) await fn(event); }
            append(...items) { this.children.push(...items); }
            appendChild(item) { this.append(item); return item; }
            replaceChildren(...items) { this.children=items; }
            setAttribute(name,value) { this[name]=value; }
            matches(selector) { return (selector === '[data-assembly-item-quantity]' && this.dataset.assemblyItemQuantity !== undefined) || (selector === '[data-assembly-item-remark]' && this.dataset.assemblyItemRemark !== undefined); }
            set innerHTML(value) { throw Error('Untrusted HTML insertion'); }
            querySelectorAll() { return []; }
          }
          const waitFor = async (condition, description, timeoutMs=3000) => {
            const started = Date.now();
            while (!condition()) {
              if (Date.now() - started > timeoutMs) throw new Error(`Timeout waiting for ${description}`);
              await new Promise(resolve => setTimeout(resolve, 10));
            }
          };
          global.document={querySelectorAll:()=>[],createElement:()=>new Element()};
          global.window={setTimeout,clearTimeout,confirm:()=>true,location:{origin:'https://test.local',assign(){}}};
          const response = data => ({ok:true,status:200,json:async()=>data});
          const initialItems = [1,2].map(id=>({manual_id:id,drawing_no:'P'+id,product_name:'零件',specification:'规格',source_kind:'bom',quantity_per_set:id+1,calculated_quantity:10*(id+1),shipped_quantity:10*(id+1),allocations:[],available_inventory:100}));
          const nodes = {};
          for (const key of ['customer','drawing','set-quantity','submit','status','preview','warning-summary','component-search','component-results','recalculate']) nodes[key]=new Element();
          nodes.customer.value='客户A'; nodes.drawing.value='ASM-100'; nodes['set-quantity'].value='10';
          const initial=new Element(); initial.textContent=JSON.stringify({items:initialItems,warnings:[],preview_token:'initial'});
          const form=new Element(); form.action='/admin/shipped-orders/assembly/new';
          form.querySelector=selector=>selector==='[data-assembly-initial-preview]' ? initial : nodes[selector.slice(15,-1)];
          const all=element=>[element,...element.children.flatMap(all)];
          global.FormData=class {
            constructor() { this.fields=all(nodes.preview).filter(el=>el.name).map(el=>[el.name,el.value]); }
            set(key,value) { this.fields=this.fields.filter(([name])=>name!==key);this.fields.push([key,value]); }
            append(key,value) { this.fields.push([key,value]); }
            delete(key) { this.fields=this.fields.filter(([name])=>name!==key); }
            getAll(key) { return this.fields.filter(([name])=>name===key).map(([,value])=>value); }
          };
          form.querySelectorAll=()=>all(nodes.preview).filter(el=>el.dataset.assemblyItemQuantity !== undefined);
          const previews=[]; const searches=[]; const submissions=[];
          global.fetch=async (url, options={})=>{
            if (options.body instanceof FormData) { submissions.push(options.body); return {ok:true,status:201,json:async()=>({redirect_url:'/admin/shipped-orders'})}; }
            if (url.includes('component-options')) return await new Promise(resolve=>searches.push({resolve,options}));
            if (url.includes('assembly-options')) return response({assembly_drawing_numbers:['ASM-B']});
            const payload=JSON.parse(options.body); previews.push(payload);
            if (Object.values(payload.overrides).some(value=>Number(value) < 0)) return {ok:false,status:400,json:async()=>({error:'数量必须为非负整数'})};
            const items=payload.selected_manual_ids.map(id=>{
              const base=initialItems.find(item=>item.manual_id===Number(id)) || {manual_id:Number(id),drawing_no:'<img onerror=evil()>',product_name:'追加',specification:'X',source_kind:'extra',quantity_per_set:0,allocations:[]};
              return {...base,shipped_quantity:Number(payload.overrides[id]),remark:payload.remarks?.[id] || '',calculated_quantity:base.quantity_per_set*Number(payload.set_quantity)};
            });
            return response({items,warnings:[],preview_token:'new'});
          };
          vm.runInThisContext(fs.readFileSync('static/assembly_shipping.js','utf8'));
          globalThis.__assemblyShippingTestApi.initializeAssemblyShipmentForm(form);
          const removeButtons=()=>all(nodes.preview).filter(el=>el.dataset.assemblyRemoveItem !== undefined);
          assert.equal(removeButtons().length,2,'each shipment row has a remove control');
          let previewCount=previews.length;
          await removeButtons()[1].emit('click');
          await waitFor(()=>previews.length===previewCount+1,'preview after removing a component');
          assert.deepEqual(previews.at(-1).selected_manual_ids.map(Number),[1]);
          let quantity=form.querySelectorAll()[0]; quantity.value='17';
          previewCount=previews.length;
          await form.emit('input',{target:quantity});
          await waitFor(()=>previews.length===previewCount+1,'preview after a quantity change');
          previewCount=previews.length;
          nodes['set-quantity'].value='20'; await nodes['set-quantity'].emit('input');
          await waitFor(()=>previews.length===previewCount+1,'preview after a set-count change');
          assert.equal(previews.at(-1).overrides['1'],'17','set changes preserve actual quantity');
          assert.deepEqual(previews.at(-1).selected_manual_ids.map(Number),[1]);
          nodes['component-search'].value='P3'; await nodes['component-search'].emit('input');
          await waitFor(()=>searches.length===1,'component search request');
          searches[0].resolve(response({items:[{manual_id:3,drawing_no:'<img onerror=evil()>',product_name:'追加',specification:'X'}]}));
          await waitFor(()=>all(nodes['component-results']).some(el=>el.dataset.assemblyAddItem !== undefined),'safe append control');
          const addButton=all(nodes['component-results']).find(el=>el.dataset.assemblyAddItem !== undefined);
          assert.ok(addButton,'search renders a safe append control');
          previewCount=previews.length;
          await addButton.emit('click');
          await waitFor(()=>previews.length===previewCount+1,'preview after appending a component');
          assert.deepEqual(previews.at(-1).selected_manual_ids.map(Number),[1,3]);
          nodes['component-search'].value='P3'; await nodes['component-search'].emit('input');
          await waitFor(()=>searches.length===2,'duplicate product search');
          searches[1].resolve(response({items:[{manual_id:3,drawing_no:'P3',product_name:'追加',specification:'X'}]}));
          await waitFor(()=>all(nodes['component-results']).some(el=>el.dataset.assemblyAddItem !== undefined),'duplicate merge control');
          previewCount=previews.length;
          await all(nodes['component-results']).find(el=>el.dataset.assemblyAddItem !== undefined).emit('click');
          await waitFor(()=>previews.length===previewCount+1,'duplicate merge preview');
          assert.deepEqual(previews.at(-1).selected_manual_ids.map(Number),[1,3]);
          assert.equal(Number(previews.at(-1).overrides['3']),2,'duplicate add merges quantity in one row');
          let remark=all(nodes.preview).find(el=>el.dataset.assemblyItemRemark !== undefined && el.dataset.manualId==='3');
          assert.ok(remark,'every row offers a remark input');
          remark.value='<script>原样保留</script>';
          previewCount=previews.length;
          await form.emit('input',{target:remark});
          await waitFor(()=>previews.length===previewCount+1,'remark preview');
          assert.equal(previews.at(-1).remarks['3'],'<script>原样保留</script>');
          quantity=form.querySelectorAll().find(el=>el.dataset.manualId==='3'); quantity.value='7';
          previewCount=previews.length;
          await form.emit('input',{target:quantity});
          await waitFor(()=>previews.length===previewCount+1,'preview after extra quantity change');
          previewCount=previews.length;
          await nodes.recalculate.emit('click');
          await waitFor(()=>previews.length===previewCount+1,'preview after explicit BOM recalculation');
          assert.equal(Number(previews.at(-1).overrides['1']),40,'explicit recalc updates BOM quantity');
          assert.equal(previews.at(-1).overrides['3'],'7','extra never multiplies by set count');
          assert.equal(previews.at(-1).remarks['3'],'<script>原样保留</script>','recalculation retains remarks');
          quantity=form.querySelectorAll().find(el=>el.dataset.manualId==='3'); quantity.value='0';
          previewCount=previews.length;
          await form.emit('input',{target:quantity});
          await waitFor(()=>previews.length===previewCount+1 && nodes.submit.disabled===false,'zero-line preview');
          quantity=form.querySelectorAll().find(el=>el.dataset.manualId==='3');
          assert.equal(quantity.min,'0');
          assert.ok(all(nodes.preview).some(el=>String(el.textContent).includes('本次不发')),'zero row is clearly labeled');
          await form.emit('submit');
          assert.deepEqual(submissions[0].getAll('shipped_quantity'),['40','0']);
          assert.deepEqual(submissions[0].getAll('line_remark'),['','<script>原样保留</script>']);
          quantity.value='-1';
          previewCount=previews.length;
          await form.emit('input',{target:quantity});
          await waitFor(()=>previews.length===previewCount+1,'rejected invalid preview');
          quantity=form.querySelectorAll().find(el=>el.dataset.manualId==='3');
          assert.ok(quantity,'invalid input must remain editable after preview rejection');
          assert.equal(nodes.submit.disabled,true);
          previewCount=previews.length;
          quantity.value='7'; await form.emit('input',{target:quantity});
          await waitFor(()=>previews.length===previewCount+1 && nodes.submit.disabled===false,'corrected preview');
          assert.equal(nodes.submit.disabled,false,'correcting invalid quantity recovers without resetting selection');
          await form.emit('submit');
          assert.deepEqual(submissions[0].getAll('selected_manual_ids'),['1','3']);
          assert.deepEqual(submissions[0].getAll('manual_id'),['1','3']);
          assert.deepEqual(submissions[1].getAll('shipped_quantity'),['40','7']);
          assert.deepEqual(submissions[0].getAll('assembly_selection_explicit'),['1']);
          previewCount=previews.length;
          await removeButtons()[0].emit('click');
          await waitFor(()=>previews.length===previewCount+1,'preview after removing the first remaining component');
          await removeButtons()[0].emit('click');
          await waitFor(()=>form._assemblySelectedIds?.length===0 && removeButtons().length===0 &&
            all(nodes.preview).map(el=>el.textContent || '').join('')==='本次明细为空，请搜索追加至少一个配件。' &&
            nodes.submit.disabled===true,'rendered explicit empty component state');
          assert.deepEqual(form._assemblySelectedIds,[]);
          assert.equal(removeButtons().length,0,'empty selection must replace stale shipment rows');
          assert.equal(nodes.submit.disabled,true);
          assert.equal(all(nodes.preview).map(el=>el.textContent || '').join(''),'本次明细为空，请搜索追加至少一个配件。');
          nodes['component-search'].value='old'; await nodes['component-search'].emit('input');
          await waitFor(()=>searches.length===3,'old-customer component search request');
          nodes.customer.value='客户B'; await nodes.customer.emit('change');
          assert.equal(searches[2].options.signal.aborted,true);
          searches[2].resolve(response({items:[{manual_id:9,drawing_no:'OLD'}]}));
          await new Promise(resolve=>setImmediate(resolve));
          assert.equal(nodes['component-results'].children.length,0,'late old customer cannot insert candidates');
          assert.equal(form._assemblySelectedIds,null,'changing customer explicitly resets adjustments');
        })().catch(error=>{console.error(error);process.exitCode=1});
        '''
        result = subprocess.run(['node', '-e', script], cwd=Path(app.__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
