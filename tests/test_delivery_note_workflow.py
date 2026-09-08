import json
import sqlite3
import tempfile
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app
import shipping_workflow
from tests.test_assembly_shipping import AssemblyTask7TestCase, VALID_PNG_BYTES, png_bytes


class DeliveryNoteWorkflowTests(AssemblyTask7TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(app, **{
            name: Path(self.files.name) / name.lower()
            for name in ('DATA_DIR', 'MANUALS_DIR', 'SIGNATURES_DIR',
                         'RECONCILIATION_SIGNATURES_DIR', 'SHIPMENT_IMAGES_DIR',
                         'INSPECTION_REPORTS_DIR', 'PRODUCTION_DRAWINGS_DIR')
        })
        self.paths.start()
        super().setUp()
        self.product = self.create_product()
        self.order = self.create_order(self.product, 'SO-DN', 20)
        self.stock_product(self.product, 20)
        self.configure_components([(self.product, 1)])
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET supplier='长规格', unit='件', unit_price_minor=0")
            conn.execute("INSERT INTO customers (name, recipient_name, recipient_phone, address, created_at, updated_at) VALUES ('客户A', '张师傅', '13800000000', '宁波仓库', '', '')")

    def tearDown(self):
        super().tearDown()
        self.paths.stop()
        self.files.cleanup()

    def ordinary_data(self, **extra):
        return dict(shipped_at='2026-09-08', order_id=str(self.order),
                    shipped_quantity='20', operation_token='ordinary-token', **extra)

    def note_rows(self):
        with app.get_db() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM delivery_notes ORDER BY id')]

    def test_ordinary_retry_returns_same_receipt_after_stock_and_customer_change(self):
        first = self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        self.assertRegex(first.location, r'/admin/delivery-notes/operations/\d+$')
        notes = self.note_rows()
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 0)
            conn.execute("UPDATE customers SET recipient_name='李师傅'")
            note = shipping_workflow.load_delivery_note(conn, notes[0]['id'])
        self.assertEqual(note['recipient_name'], '张师傅')
        self.assertEqual(note['items'][0]['specification'], '长规格')
        self.assertEqual(note['items'][0]['unit'], '件')
        retry = self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        self.assertEqual(retry.location, first.location)
        self.assertEqual([n['id'] for n in self.note_rows()], [n['id'] for n in notes])
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_order_shipments').fetchone()[0], 1)
        self.assertIn('张师傅', self.client.get(first.location).get_data(as_text=True))

    def test_token_conflict_checks_content_and_user(self):
        data = self.ordinary_data()
        self.client.post('/admin/shipped-orders/new', data=data)
        data['shipped_quantity'] = '1'
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=data).status_code, 409)
        with app.get_db() as conn:
            app.seed_user(conn, 'other-admin', 'test-password', 'admin', '2026-09-08')
        with self.client.session_transaction() as session:
            session['admin_username'] = 'other-admin'
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=self.ordinary_data()).status_code, 409)

    def test_cross_customer_groups_and_one_time_recipient_override(self):
        product = self.create_product('PB', '客户B')
        order = self.create_order(product, 'SO-B', 2, customer='客户B')
        self.stock_product(product, 2)
        data = self.ordinary_data(recipient_overrides=json.dumps({
            '客户A': {'recipient_name': '王师傅', 'recipient_phone': '123', 'address': '临时仓'},
        }))
        data.update(order_id=[str(self.order), str(order)], shipped_quantity=['2', '2'])
        self.client.post('/admin/shipped-orders/new', data=data)
        notes = self.note_rows()
        self.assertEqual([n['customer'] for n in notes], ['客户A', '客户B'])
        self.assertEqual(notes[0]['recipient_name'], '王师傅')
        self.assertEqual(notes[1]['recipient_name'], '')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT recipient_name FROM customers').fetchone()[0], '张师傅')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_note_sources').fetchone()[0], 2)

    def test_assembly_retry_uses_batch_source_and_ignores_changed_preview_state(self):
        preview = self.post_preview(sets=2).get_json()
        data = self.save_data(preview, extra_data={'operation_token': 'assembly-token'})
        first = self.client.post('/admin/shipped-orders/assembly/new', data=data)
        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertIn('/admin/delivery-notes/operations/', first.get_json()['redirect_url'])
        retry = self.client.post('/admin/shipped-orders/assembly/new', data=data)
        self.assertEqual(retry.status_code, 201, retry.get_json())
        self.assertEqual(retry.get_json(), first.get_json())
        with app.get_db() as conn:
            source = conn.execute('SELECT source_type, source_id FROM delivery_note_sources').fetchone()
            self.assertEqual(tuple(source), ('assembly', first.get_json()['batch_id']))
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 18)

    def test_attachment_digest_rejects_same_filename_different_content(self):
        data = self.ordinary_data()
        data['shipped_quantity'] = '1'
        data['images'] = (BytesIO(VALID_PNG_BYTES), 'photo.png')
        first = self.client.post('/admin/shipped-orders/new', data=data)
        self.assertEqual(first.status_code, 302)
        data['images'] = (BytesIO(png_bytes(2, 2)), 'photo.png')
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=data).status_code, 409)
        data['images'] = (BytesIO(VALID_PNG_BYTES), 'photo.png')
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=data).location, first.location)
        self.assertEqual(len(list(app.SHIPMENT_IMAGES_DIR.iterdir())), 1)

    def test_note_creation_failure_rolls_back_stock_images_and_operation_allows_retry(self):
        for mode in ['ordinary', 'assembly']:
            with self.subTest(mode=mode):
                route = '/admin/shipped-orders/new' if mode == 'ordinary' else '/admin/shipped-orders/assembly/new'
                data = self.ordinary_data() if mode == 'ordinary' else self.save_data(self.post_preview(sets=2).get_json(), extra_data={'operation_token': 'assembly-fail'})
                data['images'] = (BytesIO(VALID_PNG_BYTES), 'photo.png')
                with patch.object(app, 'create_delivery_notes', side_effect=sqlite3.OperationalError('injected note write failure')):
                    response = self.client.post(route, data=data)
                self.assertEqual(response.status_code, 500)
                with app.get_db() as conn:
                    for table in ['delivery_operations', 'delivery_notes', 'delivery_note_sources', 'product_order_shipments', 'assembly_shipment_batches']:
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
                    self.assertEqual(app.inventory_total_for_manual(conn, self.product), 20)
                self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])
        data = self.ordinary_data()
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=data).status_code, 302)
        self.assertEqual(len(self.note_rows()), 1)

    def test_ordinary_edit_updates_note_and_delete_invalidates_download(self):
        data = self.ordinary_data()
        data['shipped_quantity'] = '2'
        self.client.post('/admin/shipped-orders/new', data=data)
        note_id = self.note_rows()[0]['id']
        with app.get_db() as conn:
            sid = conn.execute('SELECT id FROM product_order_shipments').fetchone()[0]
            conn.execute("UPDATE delivery_notes SET updated_at='2000-01-01'")
        self.client.post(f'/admin/shipped-orders/{sid}/edit', data={'shipped_quantity': '3', 'shipped_at': '2026-09-09'})
        with app.get_db() as conn:
            note = shipping_workflow.load_delivery_note(conn, note_id)
        self.assertEqual(note['items'][0]['shipped_quantity'], 3)
        self.assertNotEqual(note['updated_at'], '2000-01-01')
        self.client.post(f'/admin/shipped-orders/{sid}/delete')
        self.assertEqual(self.client.get(f'/admin/delivery-notes/{note_id}.pdf').status_code, 410)
        self.assertEqual(self.note_rows()[0]['invalidated'], 1)

    def test_pdf_retry_is_read_only_and_permission_protected(self):
        self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        note = self.note_rows()[0]
        route = f"/admin/delivery-notes/{note['id']}.pdf"
        with patch.object(app, 'build_shipped_orders_pdf', side_effect=OSError('render failure')):
            self.assertEqual(self.client.get(route).status_code, 503)
        pdf = self.client.get(route)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b'%PDF'))
        self.assertEqual(self.note_rows(), [note])
        with self.client.session_transaction() as session:
            session.clear()
        self.assertNotEqual(self.client.get(route).status_code, 200)

    def test_assembly_item_replacement_keeps_note_and_delete_invalidates(self):
        preview = self.post_preview(sets=2).get_json()
        first = self.client.post('/admin/shipped-orders/assembly/new', data=self.save_data(preview))
        batch_id = first.get_json()['batch_id']
        note_id = self.note_rows()[0]['id']
        with app.get_db() as conn:
            item_id = conn.execute('SELECT id FROM assembly_shipment_items').fetchone()[0]
        edit_route = f'/admin/shipped-orders/assembly/{batch_id}/edit'
        edited_preview = self.client.post(edit_route, json={
            'customer': '客户A', 'assembly_drawing_no': 'ASM-100', 'set_quantity': 3,
            'selected_manual_ids': [self.product], 'overrides': {self.product: 3}}).get_json()
        edited = self.client.post(edit_route, data=self.save_data(edited_preview))
        self.assertEqual(edited.status_code, 201, edited.get_json())
        with app.get_db() as conn:
            self.assertNotEqual(conn.execute('SELECT id FROM assembly_shipment_items').fetchone()[0], item_id)
            note = shipping_workflow.load_delivery_note(conn, note_id)
        self.assertEqual(note['invalidated'], 0)
        self.assertEqual(note['items'][0]['shipped_quantity'], 3)
        self.assertEqual(note['recipient_name'], '张师傅')
        self.assertEqual(self.client.get(f'/admin/delivery-notes/{note_id}.pdf').status_code, 200)
        self.client.post(f'/admin/shipped-orders/assembly/{batch_id}/delete')
        self.assertEqual(self.client.get(f'/admin/delivery-notes/{note_id}.pdf').status_code, 410)

    def test_saved_note_link_is_reachable_from_history(self):
        self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        note = self.note_rows()[0]
        html = self.client.get('/admin/shipped-orders').get_data(as_text=True)
        self.assertIn(f'/admin/delivery-notes/operations/{note["operation_id"]}', html)
        self.assertIn(note['document_no'], html)

    def test_legacy_pdf_keeps_specification_unit_and_warns_missing_recipient(self):
        self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        with app.get_db() as conn:
            shipments = app.fetch_shipped_orders(conn)
        self.assertIn('unit', shipments[0].keys())
        self.assertEqual(shipments[0]['unit'], '件')
        from tests.test_shipped_pdf_company import ShippedPdfCompanyTests
        story = []
        with patch.object(app.SimpleDocTemplate, 'build', lambda _doc, values: story.extend(values)):
            app.build_shipped_orders_pdf(shipments, '', '客户A', '')
        text = ShippedPdfCompanyTests.collect_story_text(story)
        for content in ['长规格', '件', '收货资料未完整填写']:
            self.assertIn(content, text)
        self.assertNotIn('单价', text)

    def test_committed_context_exit_error_preserves_receipt_files_and_retry(self):
        for mode in ['ordinary', 'assembly']:
            with self.subTest(mode=mode):
                route = '/admin/shipped-orders/new' if mode == 'ordinary' else '/admin/shipped-orders/assembly/new'
                data = self.ordinary_data() if mode == 'ordinary' else self.save_data(self.post_preview(sets=2).get_json(), extra_data={'operation_token': 'assembly-exit'})
                if mode == 'ordinary':
                    data['shipped_quantity'] = '1'
                original_get_db = app.get_db

                @contextmanager
                def fail_after_commit():
                    with original_get_db() as conn:
                        before = conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0]
                        yield conn
                        after = conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0]
                    if after > before:
                        raise RuntimeError('injected committed context exit')

                data['images'] = (BytesIO(VALID_PNG_BYTES), 'exit.png')
                with patch.object(app, 'get_db', fail_after_commit):
                    first = self.client.post(route, data=data)
                self.assertEqual(first.status_code, 302 if mode == 'ordinary' else 201)
                data['images'] = (BytesIO(VALID_PNG_BYTES), 'exit.png')
                retry = self.client.post(route, data=data)
                self.assertEqual(retry.location, first.location)
                self.assertEqual(retry.get_json(silent=True), first.get_json(silent=True))
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 17)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 2)
        self.assertEqual(len(list(app.SHIPMENT_IMAGES_DIR.iterdir())), 2)

    def test_no_shipment_permission_cannot_read_result_or_pdf(self):
        first = self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        note = self.note_rows()[0]
        with app.get_db() as conn:
            conn.execute("""INSERT INTO users (username, password_hash, role, active,
                can_view_shipped, can_manage_shipped, created_at, updated_at)
                VALUES ('no-shipped', 'unused', 'operator', 1, 0, 0, '', '')""")
        with self.client.session_transaction() as session:
            session['admin_username'] = 'no-shipped'
            session['admin_role'] = 'operator'
        for route in [first.location, f'/admin/delivery-notes/{note["id"]}.pdf']:
            self.assertNotEqual(self.client.get(route).status_code, 200)

    def test_concurrent_same_intent_creates_only_one_operation_per_mode(self):
        for mode in ['ordinary', 'assembly']:
            with self.subTest(mode=mode):
                data = self.ordinary_data() if mode == 'ordinary' else self.save_data(self.post_preview(sets=2).get_json(), extra_data={'operation_token': 'concurrent-assembly'})
                if mode == 'ordinary':
                    data['shipped_quantity'] = '2'
                route = '/admin/shipped-orders/new' if mode == 'ordinary' else '/admin/shipped-orders/assembly/new'
                def submit(_):
                    client = app.app.test_client()
                    with client.session_transaction() as session:
                        session.update(admin_logged_in=True, admin_username='admin', admin_role='admin')
                    response = client.post(route, data=data)
                    return response.status_code, response.location, response.get_json(silent=True)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(submit, [1, 2]))
                self.assertEqual(responses[0], responses[1])
                self.assertEqual(responses[0][0], 302 if mode == 'ordinary' else 201)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM delivery_operations').fetchone()[0], 2)
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 16)

    def test_completed_assembly_retry_survives_connection_exit_error(self):
        data = self.save_data(self.post_preview(sets=2).get_json(), extra_data={'operation_token': 'retry-exit'})
        first = self.client.post('/admin/shipped-orders/assembly/new', data=data)
        original_get_db = app.get_db

        @contextmanager
        def fail_after_receipt_lookup():
            with original_get_db() as conn:
                yield conn
                was_transaction = conn.in_transaction
            if was_transaction:
                raise OSError('receipt lookup connection exit')

        with patch.object(app, 'get_db', fail_after_receipt_lookup):
            retry = self.client.post('/admin/shipped-orders/assembly/new', data=data)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.get_json(), first.get_json())

    def test_result_contains_only_current_operation_and_no_price_fields(self):
        first = self.client.post('/admin/shipped-orders/new', data={**self.ordinary_data(), 'shipped_quantity': '2'})
        second = self.client.post('/admin/shipped-orders/new', data={**self.ordinary_data(), 'operation_token': 'second', 'shipped_quantity': '3'})
        notes = self.note_rows()
        html = self.client.get(second.location).get_data(as_text=True)
        self.assertIn(notes[1]['document_no'], html)
        self.assertNotIn(notes[0]['document_no'], html)
        self.assertNotIn('单价', html)
        self.assertNotEqual(first.location, second.location)

    def test_invalid_image_and_inventory_failure_leave_no_receipt_and_retry(self):
        data = self.ordinary_data()
        data['images'] = (BytesIO(b'not an image'), 'broken.png')
        self.client.post('/admin/shipped-orders/new', data=data)
        with patch.object(app, 'deduct_inventory_for_shipment', side_effect=sqlite3.OperationalError('inventory write failed')):
            self.assertEqual(self.client.post('/admin/shipped-orders/new', data=self.ordinary_data()).status_code, 500)
        with app.get_db() as conn:
            for table in ['delivery_operations', 'delivery_notes', 'delivery_note_sources', 'product_order_shipments']:
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(app.inventory_total_for_manual(conn, self.product), 20)
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])
        self.assertEqual(self.client.post('/admin/shipped-orders/new', data=self.ordinary_data()).status_code, 302)

    def test_ordinary_recipient_browser_defaults_grouping_and_edits(self):
        html = self.client.get('/admin/shipped-orders/create').get_data(as_text=True)
        defaults = re.search(r'data-recipient-defaults>(.*?)</script>', html, re.S)[1]
        script = next(text for text in re.findall(r'<script>(.*?)</script>', html, re.S) if 'data-shipment-create-form' in text)
        harness = r'''
        const vm=require('vm'), assert=require('assert');
        const payload=JSON.parse(require('fs').readFileSync(0,'utf8'));
        class Element {
          constructor(){this.value='';this.children=[];this.listeners={};this.dataset={};}
          addEventListener(type, fn){(this.listeners[type] ||= []).push(fn);}
          emit(type){for(const fn of this.listeners[type] || []) fn();}
          append(...items){this.children.push(...items);}
          replaceChildren(...items){this.children=items;}
          removeAttribute(name){delete this[name];}
          set innerHTML(value){throw Error('unsafe HTML');}
        }
        const keys=['lines','add-shipment-line','order-search','customer-search','product-search','recipient-defaults','recipient-groups','recipient-overrides'];
        const nodes=Object.fromEntries(keys.map(key=>[key,new Element()]));
        nodes['recipient-defaults'].textContent=payload.defaults;
        let rows=[];
        function line(){
          const select=new Element(),quantity=new Element(),remove=new Element();
          select.options=[{value:'',dataset:{}},{value:'1',dataset:{customer:'客户A',unshipped:'20'}},{value:'2',dataset:{customer:'客户B',unshipped:'20'}}];
          Object.defineProperty(select,'selectedOptions',{get:()=>[select.options.find(o=>o.value===select.value)]});
          const row={select,querySelector:s=>s.includes('order-select')?select:s.includes('quantity')?quantity:remove,remove:()=>{rows=rows.filter(r=>r!==row)}};
          return row;
        }
        rows=[line(),line()];
        const form={querySelectorAll:()=>rows,querySelector:s=>{
          const name=s.slice(1,-1).replace(/^data-shipment-/,'').replace(/^data-/,'');
          return nodes[name];
        }};
        global.document={querySelector:()=>form,createElement:()=>new Element()};
        vm.runInThisContext(payload.script);
        rows[0].select.value='1'; rows[0].select.emit('change');
        assert.equal(nodes['recipient-groups'].children.length,1);
        let active=JSON.parse(nodes['recipient-overrides'].value);
        assert.equal(active['客户A'].recipient_name,'张师傅');
        const all=el=>[el,...el.children.flatMap(all)];
        const nameInput=all(nodes['recipient-groups']).find(el=>el.value==='张师傅');
        nameInput.value='临时收货人';nameInput.emit('input');
        rows[1].select.value='2'; rows[1].select.emit('change');
        active=JSON.parse(nodes['recipient-overrides'].value);
        assert.equal(nodes['recipient-groups'].children.length,2);
        assert.equal(active['客户A'].recipient_name,'临时收货人');
        assert.equal(active['客户B'].recipient_name,'');
        rows[1].querySelector('[data-remove-shipment-line]').emit('click');
        active=JSON.parse(nodes['recipient-overrides'].value);
        assert.deepEqual(Object.keys(active),['客户A']);
        assert.equal(active['客户A'].recipient_name,'临时收货人');
        '''
        result = subprocess.run(['node', '-e', harness], input=json.dumps({'defaults': defaults, 'script': script}), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_assembly_note_uses_actual_allocated_orders_not_assembly_drawing_number(self):
        self.create_order(self.product, 'SO-SECOND', 10)
        preview = self.post_preview(sets=25).get_json()
        self.client.post('/admin/shipped-orders/assembly/new', data=self.save_data(preview))
        note_id = self.note_rows()[0]['id']
        with app.get_db() as conn:
            note = shipping_workflow.load_delivery_note(conn, note_id)
        self.assertIn('SO-DN', note['items'][0]['order_no'])
        self.assertIn('SO-SECOND', note['items'][0]['order_no'])
        self.assertNotIn('ASM-100', note['items'][0]['order_no'])
        extra = self.create_product('NO-ORDER')
        preview = self.client.post('/admin/shipped-orders/assembly-preview', json={
            'customer': '客户A', 'assembly_drawing_no': 'ASM-100', 'set_quantity': 1,
            'selected_manual_ids': [extra], 'overrides': {extra: 1}}).get_json()
        response = self.client.post('/admin/shipped-orders/assembly/new', data=self.save_data(
            preview, extra_data={'selected_manual_ids': [str(extra)]}))
        self.assertEqual(response.status_code, 201, response.get_json())
        last_note_id = self.note_rows()[-1]['id']
        with app.get_db() as conn:
            note = shipping_workflow.load_delivery_note(conn, last_note_id)
        self.assertEqual(note['items'][0]['order_no'], '未关联订单')

    def test_delivery_timestamp_display_converts_utc_without_changing_snapshot(self):
        self.client.post('/admin/shipped-orders/new', data=self.ordinary_data())
        note = self.note_rows()[0]
        with app.get_db() as conn:
            conn.execute("UPDATE delivery_notes SET updated_at='2026-09-08T15:03:25.123456+00:00' WHERE id=?", (note['id'],))
            note = shipping_workflow.load_delivery_note(conn, note['id'])
        from tests.test_shipped_pdf_company import ShippedPdfCompanyTests
        story = []
        with patch.object(app.SimpleDocTemplate, 'build', lambda _doc, values: story.extend(values)):
            app.build_shipped_orders_pdf(note['items'], '', note['customer'], '', recipient_metadata=note)
        text = ShippedPdfCompanyTests.collect_story_text(story)
        self.assertIn('2026-09-08 23:03:25（北京时间）', text)
        html = self.client.get(f'/admin/delivery-notes/operations/{note["operation_id"]}').get_data(as_text=True)
        self.assertIn('2026-09-08 23:03:25（北京时间）', html)
        self.assertEqual(self.note_rows()[0]['updated_at'], '2026-09-08T15:03:25.123456+00:00')

    def save_both_note_modes(self):
        self.client.post('/admin/shipped-orders/new', data={**self.ordinary_data(), 'shipped_quantity': '2'})
        response = self.client.post('/admin/shipped-orders/assembly/new', data=self.save_data(self.post_preview(sets=2).get_json()))
        self.assertEqual(response.status_code, 201)
        return self.note_rows()

    def rename_customer(self, name):
        with app.get_db() as conn:
            customer_id = conn.execute("SELECT id FROM customers WHERE name='客户A'").fetchone()[0]
        return self.client.post(f'/admin/customers/{customer_id}/edit', data={
            'name': name, 'recipient_name': '新联系人', 'recipient_phone': '456', 'address': '新地址'})

    def test_customer_rename_preserves_identity_and_frozen_display_for_both_modes(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute("UPDATE delivery_notes SET updated_at='2000-01-01'")
        self.assertEqual(self.rename_customer('客户新名称').status_code, 302)
        for saved in notes:
            with app.get_db() as conn:
                note = shipping_workflow.load_delivery_note(conn, saved['id'])
            self.assertEqual(note['invalidated'], 0)
            self.assertEqual(note['customer'], '客户A')
            self.assertEqual(note['recipient_name'], '张师傅')
            self.assertEqual(note['recipient_phone'], '13800000000')
            self.assertEqual(note['address'], '宁波仓库')
            self.assertNotEqual(note['updated_at'], '2000-01-01')
            self.assertEqual(self.client.get(f'/admin/delivery-notes/{saved["id"]}.pdf').status_code, 200)

    def test_true_customer_reassignment_invalidates_both_note_modes(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute("INSERT INTO customers (name,created_at,updated_at) VALUES ('客户B','','')")
            conn.execute("UPDATE product_orders SET customer='客户B'")
            conn.execute("UPDATE assembly_shipment_batches SET customer='客户B'")
        for note in notes:
            self.assertEqual(self.client.get(f'/admin/delivery-notes/{note["id"]}.pdf').status_code, 410)

    def test_customer_id_does_not_fall_back_to_same_name_after_identity_replacement(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute("DELETE FROM customers WHERE name='客户A'")
            conn.execute("INSERT INTO customers (name,created_at,updated_at) VALUES ('客户A','','')")
        for note in notes:
            self.assertEqual(self.client.get(f'/admin/delivery-notes/{note["id"]}.pdf').status_code, 410)

    def test_null_legacy_notes_bind_only_valid_ownership_during_controlled_rename(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute('UPDATE delivery_notes SET customer_id=NULL')
        self.rename_customer('客户新名称')
        for saved in notes:
            with app.get_db() as conn:
                note = shipping_workflow.load_delivery_note(conn, saved['id'])
            self.assertEqual(note['invalidated'], 0)
            self.assertIsNotNone(note['customer_id'])
            self.assertEqual(note['customer'], '客户A')
            self.assertEqual(self.client.get(f'/admin/delivery-notes/{saved["id"]}.pdf').status_code, 200)

    def test_unproven_or_invalidated_legacy_note_is_not_rebound_on_rename(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute('UPDATE delivery_notes SET customer_id=NULL')
            conn.execute("UPDATE product_orders SET customer='另一客户'")
            conn.execute('UPDATE delivery_notes SET invalidated=1 WHERE id=?', (notes[1]['id'],))
        self.rename_customer('客户新名称')
        for saved in notes:
            with app.get_db() as conn:
                note = shipping_workflow.load_delivery_note(conn, saved['id'])
            self.assertEqual(note['invalidated'], 1)
            self.assertIsNone(note['customer_id'])

    def test_legacy_identity_binding_rolls_back_with_failed_customer_rename(self):
        self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute('UPDATE delivery_notes SET customer_id=NULL')
            conn.execute("INSERT INTO customers (name,created_at,updated_at) VALUES ('重名客户','','')")
        self.rename_customer('重名客户')
        with app.get_db() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM customers WHERE name='客户A'").fetchone())
            for row in conn.execute('SELECT customer_id FROM delivery_notes'):
                self.assertIsNone(row['customer_id'])

    def test_unregistered_and_nameless_customer_notes_use_strict_name_fallback(self):
        # Complete the app's first-request customer backfill before simulating
        # a name-only source with no customer master record.
        self.client.get('/admin/shipped-orders')
        with app.get_db() as conn:
            conn.execute("DELETE FROM customers WHERE name='客户A'")
        notes = self.save_both_note_modes()
        for saved in notes:
            with app.get_db() as conn:
                note = shipping_workflow.load_delivery_note(conn, saved['id'])
            self.assertIsNone(note['customer_id'])
            self.assertEqual(note['invalidated'], 0)
        with app.get_db() as conn:
            conn.execute("UPDATE product_orders SET customer=''")
            conn.execute("UPDATE manuals SET customer=''")
        response = self.client.post('/admin/shipped-orders/new', data={
            **self.ordinary_data(), 'shipped_quantity': '1', 'operation_token': 'nameless'})
        self.assertEqual(response.status_code, 302)
        nameless_id = self.note_rows()[-1]['id']
        with app.get_db() as conn:
            note = shipping_workflow.load_delivery_note(conn, nameless_id)
            self.assertEqual(note['customer'], '')
            self.assertEqual(note['invalidated'], 0)
            conn.execute("UPDATE product_orders SET customer='新归属'")
        self.assertEqual(self.client.get(f'/admin/delivery-notes/{nameless_id}.pdf').status_code, 410)

    def test_customer_identity_migration_is_additive_and_idempotent(self):
        notes = self.save_both_note_modes()
        with app.get_db() as conn:
            conn.execute('ALTER TABLE delivery_notes DROP COLUMN customer_id')
            before = [dict(row) for row in conn.execute('SELECT * FROM delivery_notes')]
            shipping_workflow.ensure_shipping_workflow_tables(conn)
            shipping_workflow.ensure_shipping_workflow_tables(conn)
            after = [dict(row) for row in conn.execute('SELECT * FROM delivery_notes')]
        for old, migrated in zip(before, after):
            self.assertIsNone(migrated.pop('customer_id'))
            self.assertEqual(old, migrated)
        for saved in notes:
            self.assertEqual(self.client.get(f'/admin/delivery-notes/{saved["id"]}.pdf').status_code, 200)
