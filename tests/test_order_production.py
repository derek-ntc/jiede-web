import unittest

import app
from tests import test_production_processes as fixtures


class OrderProductionTests(unittest.TestCase):
    setUp = fixtures.ProductionProcessPersistenceTests.setUp
    tearDown = fixtures.ProductionProcessPersistenceTests.tearDown
    _insert_manual = staticmethod(fixtures.ProductionProcessPersistenceTests._insert_manual)

    def order(self, conn, number='SO1', quantity=200):
        return conn.execute("""INSERT INTO product_orders
            (manual_id, order_no, customer, ordered_at, quantity, planned_ship_at, created_at, updated_at)
            VALUES (?, ?, '客户A', '2026-09-12', ?, '2026-09-30', 'now', 'now')""",
            (self.manual_id, number, quantity)).lastrowid

    def service(self):
        import order_production
        return order_production

    def test_schema_is_additive_and_start_is_idempotent_and_independent(self):
        with app.get_db() as conn:
            columns = {r['name'] for r in conn.execute('PRAGMA table_info(production_followups)')}
            self.assertIn('order_id', columns)
            service = self.service()
            old = fixtures.ProductionProcessPersistenceTests._insert_followup(conn, manual_id=self.manual_id, laser='old-time')
            first, second = self.order(conn), self.order(conn, 'SO2')
            card = service.start_order_followup(conn, first, 'admin', 'now')
            self.assertEqual(service.start_order_followup(conn, first, 'admin', 'now'), card)
            self.assertNotEqual(service.start_order_followup(conn, second, 'admin', 'now'), card)
            service.ensure_order_production_schema(conn)
            row = conn.execute('SELECT * FROM production_followups WHERE id=?', (old,)).fetchone()
            self.assertIsNone(row['order_id'])
            self.assertEqual(row['laser_completed_at'], 'old-time')

    def test_cumulative_quantity_validates_version_bounds_and_owner(self):
        self.assertEqual(self.client.get('/admin/production-followups/orders/999').status_code, 404)
        with app.get_db() as conn:
            self.assertIn('order_id', {r['name'] for r in conn.execute('PRAGMA table_info(production_followups)')})
        service = self.service()
        with app.get_db() as conn:
            first, second = self.order(conn), self.order(conn, 'SO2')
            card = service.start_order_followup(conn, first, 'admin', 'now')
            other = service.start_order_followup(conn, second, 'admin', 'now')
            step = conn.execute('SELECT id FROM production_followup_process_steps WHERE followup_id=?', (card,)).fetchone()[0]
            service.set_process_quantity(conn, card, step, '80', '0', 'admin', 't1')
            service.set_process_quantity(conn, card, step, '80', '1', 'admin', 't2')
            before = conn.execute('SELECT COUNT(*) FROM order_production_quantity_audit').fetchone()[0]
            for qty, version, owner in [('-1', 2, card), ('1.5', 2, card), ('9223372036854775808', 2, card), ('90', 0, card), ('90', 2, other)]:
                with self.assertRaises(ValueError):
                    service.set_process_quantity(conn, owner, step, qty, version, 'admin', 'bad')
            row = conn.execute('SELECT * FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()
            self.assertEqual(row['completed_quantity'], 80)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM order_production_quantity_audit').fetchone()[0], before)
            with self.assertRaises(ValueError):
                service.validate_order_production_change(conn, first, self.manual_id, 50)
            with self.assertRaises(ValueError):
                service.validate_order_production_change(conn, first, 999, 200)

    def test_default_pages_are_read_only_and_start_requires_csrf(self):
        with app.get_db() as conn:
            order = self.order(conn)
        page = self.client.get('/admin/production-followups')
        self.assertIn('SO1', page.get_data(as_text=True))
        detail = self.client.get(f'/admin/production-followups/orders/{order}')
        self.assertIn('开始跟进', detail.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM production_followups').fetchone()[0], 0)
        path = f'/admin/production-followups/orders/{order}/start'
        self.assertEqual(self.client.post(path, headers={'X-CSRF-Token': ''}).status_code, 403)
        self.assertEqual(self.client.post(path).status_code, 302)
        self.assertEqual(self.client.post(path).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM production_followups').fetchone()[0], 1)

    def started(self, quantity=200):
        with app.get_db() as conn:
            order = self.order(conn, quantity=quantity)
            card = self.service().start_order_followup(conn, order, 'admin', 'now')
            step = conn.execute('SELECT id FROM production_followup_process_steps WHERE followup_id=? ORDER BY id', (card,)).fetchone()[0]
        return order, card, step

    def test_linked_legacy_actions_cannot_bypass_quantity_version_or_reset_confirmation(self):
        order, card, step = self.started()
        base = f'/admin/production-followups/{card}/processes/{step}'
        self.client.post(base + '/complete')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT completed_at FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()[0], '')
        self.client.post(base + '/complete', data={'version': '0'})
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()
            self.assertEqual(row['completed_quantity'], 200)
        self.client.post(base + '/revert', data={'version': '1'})
        self.client.post(f'/admin/production-followups/{card}/laser')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT completed_quantity FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()[0], 200)
        self.client.post(base + '/revert', data={'version': '1', 'confirm_reset': '1'})
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT completed_quantity FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()[0], 0)

    def test_order_edit_rejects_small_demand_and_product_swap_then_invalidates_old_forms(self):
        order, card, step = self.started(quantity=80)
        with app.get_db() as conn:
            self.service().set_process_quantity(conn, card, step, 80, 0, 'admin', 'finished')
        payload = dict(manual_id=self.manual_id, order_no='SO1', ordered_at='2026-09-12', quantity=50, planned_ship_at='2026-09-30')
        self.client.post(f'/admin/orders/{order}/edit', data=payload)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT quantity FROM product_orders WHERE id=?', (order,)).fetchone()[0], 80)
            other = self._insert_manual(conn, 'SWAP', '不能替换')
        self.client.post(f'/admin/orders/{order}/edit', data={**payload, 'manual_id': other, 'quantity': 80})
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT manual_id FROM product_orders WHERE id=?', (order,)).fetchone()[0], self.manual_id)
        self.client.post(f'/admin/orders/{order}/edit', data={**payload, 'quantity': 200})
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()
            self.assertEqual((row['completed_quantity'], row['completed_at'], row['version']), (80, '', 2))
            with self.assertRaises(ValueError):
                self.service().set_process_quantity(conn, card, step, 90, 1, 'admin', 'stale')
        page = self.client.get(f'/admin/production-followups/orders/{order}').get_data(as_text=True)
        self.assertIn('40.0%', page)
        self.assertIn('80 / 200', page)

    def test_delete_guards_audit_retention_and_customer_identity(self):
        order, card, step = self.started()
        self.client.post(f'/admin/orders/{order}/delete')
        with app.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM product_orders WHERE id=?', (order,)).fetchone())
            with self.assertRaises(ValueError):
                app.delete_manuals([self.manual_id])
            self.service().set_process_quantity(conn, card, step, 80, 0, 'admin', 't1')
        self.client.post(f'/admin/production-followups/{card}/customer', data={'customer': ''})
        self.client.post(f'/admin/production-followups/{card}/processes/{step}/delete')
        with app.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM production_followup_process_steps WHERE id=?', (step,)).fetchone())
            self.assertEqual(conn.execute('SELECT customer FROM production_followups WHERE id=?', (card,)).fetchone()[0], '客户A')
        self.client.post(f'/admin/production-followups/{card}/processes/{step}/delete', data={'confirm_quantity_delete': '1'})
        self.client.post(f'/admin/production-followups/{card}/delete', data={'confirm_unlink': '1'})
        with app.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM production_followup_process_steps WHERE id=?', (step,)).fetchone())
            self.assertIsNotNone(conn.execute('SELECT id FROM production_followups WHERE id=?', (card,)).fetchone())
            audit = conn.execute('SELECT * FROM order_production_quantity_audit WHERE step_id=? ORDER BY id', (step,)).fetchall()
            self.assertEqual([(r['event'], r['before_quantity'], r['after_quantity']) for r in audit], [('quantity', 0, 80), ('delete', 80, 80)])

    def test_reset_does_not_allow_card_delete_but_untouched_confirmed_card_can_unlink(self):
        order, card, step = self.started()
        with app.get_db() as conn:
            self.service().set_process_quantity(conn, card, step, 80, 0, 'admin', 't1')
            self.service().set_process_quantity(conn, card, step, 0, 1, 'admin', 't2')
        self.client.post(f'/admin/production-followups/{card}/delete', data={'confirm_unlink': '1'})
        with app.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM production_followups WHERE id=?', (card,)).fetchone())
        other_order, other_card, _ = self.started()
        self.client.post(f'/admin/production-followups/{other_card}/delete')
        with app.get_db() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM production_followups WHERE id=?', (other_card,)).fetchone())
        self.client.post(f'/admin/production-followups/{other_card}/delete', data={'confirm_unlink': '1'})
        with app.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM production_followups WHERE id=?', (other_card,)).fetchone())

    def test_independent_processes_allow_parallel_completion_and_reorder(self):
        order, card, first = self.started()
        with app.get_db() as conn:
            last = conn.execute('SELECT id FROM production_followup_process_steps WHERE followup_id=? ORDER BY id DESC', (card,)).fetchone()[0]
        self.client.post(f'/admin/production-followups/{card}/processes/{last}/complete', data={'version': 0})
        self.client.post(f'/admin/production-followups/{card}/processes/{last}/move', data={'direction': 'up'})
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM production_followup_process_steps WHERE id=?', (last,)).fetchone()
            self.assertEqual((row['completed_quantity'], row['sort_order']), (200, 1))
        preview = self.client.get(f'/admin/production-followups/{card}/process-card').get_data(as_text=True)
        self.assertIn('SO1', preview)
        self.assertIn('200 / 200', preview)

    def test_mutations_keep_order_detail_return_context(self):
        order, card, step = self.started()
        response = self.client.post(f'/admin/production-followups/{card}/processes/{step}/quantity',
            data={'quantity': '80', 'version': '0', 'filter_q': 'P-100', 'filter_customer': '客户A', 'filter_sort': 'order_no', 'filter_direction': 'asc'})
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query, {'q': ['P-100'], 'customer': ['客户A'], 'sort': ['order_no'], 'direction': ['asc']})

    def test_permissions_and_cross_card_route_and_no_inventory_side_effects(self):
        order, card, step = self.started()
        other_order, other_card, other_step = self.started()
        with app.get_db() as conn:
            before = {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')] for table in ['inventory_balances', 'inventory_transactions', 'product_order_shipments']}
            conn.execute("""INSERT INTO users (username, password_hash, role, active, can_manage_production_followups, can_view_orders, can_manage_orders, created_at, updated_at)
                VALUES ('progress-reader', 'unused', 'operator', 1, 0, 0, 0, 'now', 'now')""")
        self.client.post(f'/admin/production-followups/{card}/processes/{other_step}/quantity', data={'quantity': '80', 'version': '0'})
        with self.client.session_transaction() as session:
            session['admin_username'] = 'progress-reader'
            session['admin_role'] = 'operator'
        html = self.client.get(f'/admin/production-followups/orders/{order}').get_data(as_text=True)
        self.assertNotIn('name="quantity"', html)
        self.assertNotIn('/edit', html)
        self.assertNotEqual(self.client.get(f'/admin/orders/groups/{order}').status_code, 200)
        for path, data in [(f'/admin/production-followups/orders/{order}/start', {}),
                           (f'/admin/production-followups/{card}/processes/{step}/quantity', {'quantity': '80', 'version': '0'})]:
            self.assertNotEqual(self.client.post(path, data=data).status_code, 200)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT SUM(completed_quantity) FROM production_followup_process_steps').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM order_production_quantity_audit').fetchone()[0], 0)
            for table, rows in before.items():
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')], rows)

    def test_concurrent_start_and_quantity_versions_are_serialized(self):
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with app.get_db() as conn:
            order = self.order(conn)
        barrier = Barrier(2)
        def start():
            with sqlite3.connect(app.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                barrier.wait(timeout=5)
                return self.service().start_order_followup(conn, order, 'worker', 'now')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start) for _ in range(2)]
            ids = [future.result() for future in futures]
        self.assertEqual(ids[0], ids[1])
        with app.get_db() as conn:
            step = conn.execute('SELECT id FROM production_followup_process_steps WHERE followup_id=?', (ids[0],)).fetchone()[0]
        barrier = Barrier(2)
        def write(quantity):
            with sqlite3.connect(app.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                barrier.wait(timeout=5)
                try:
                    self.service().set_process_quantity(conn, ids[0], step, quantity, 0, 'worker', 'now')
                    return 'saved'
                except ValueError:
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, quantity) for quantity in [80, 90]]
            self.assertCountEqual([future.result() for future in futures], ['saved', 'conflict'])
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM order_production_quantity_audit').fetchone()[0], 1)

    def test_linked_empty_template_and_migration_preserve_snapshots_and_file_references(self):
        import production_processes
        with app.get_db() as conn:
            production_processes.save_manual_process_template(conn, self.manual_id, [])
        order, card, step = (None, None, None)
        with app.get_db() as conn:
            order = self.order(conn)
            card = self.service().start_order_followup(conn, order, 'admin', 'now')
            conn.execute("INSERT INTO production_followup_files (followup_id, filename, original_filename, created_at) VALUES (?, 'kept.pdf', 'kept.pdf', 'now')", (card,))
            before = {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')] for table in ['product_orders', 'production_followups', 'production_followup_files', 'production_followup_process_steps', 'inventory_balances', 'inventory_transactions']}
        app.init_db()
        app.init_db()
        with app.get_db() as conn:
            for table, rows in before.items():
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM {table}')], rows)
        html = self.client.get(f'/admin/production-followups/orders/{order}').get_data(as_text=True)
        self.assertIn('未配置工艺', html)
        self.assertNotIn('100.0%', html)

    def test_legacy_create_and_delete_forms_submit_the_required_csrf(self):
        import re
        with app.get_db() as conn:
            fixtures.ProductionProcessPersistenceTests._insert_followup(conn, manual_id=self.manual_id)
        html = self.client.get('/admin/production-followups?view=legacy').get_data(as_text=True)
        forms = re.findall(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', html, re.S)
        self.assertGreater(len(forms), 2)
        for form in forms:
            self.assertIn('name="production_followup_csrf_token"', form)

    def test_demand_decrease_to_recorded_quantity_sets_completion_operator(self):
        order, card, step = self.started()
        with app.get_db() as conn:
            self.service().set_process_quantity(conn, card, step, 80, 0, 'worker', 't1')
        self.client.post(f'/admin/orders/{order}/edit', data=dict(manual_id=self.manual_id,
            order_no='SO1', ordered_at='2026-09-12', quantity=80, planned_ship_at='2026-09-30'))
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()
            self.assertTrue(row['completed_at'])
            self.assertEqual(row['completed_by'], 'admin')

    def test_legacy_batch_label_collision_and_oversized_version_are_safe(self):
        with app.get_db() as conn:
            old = fixtures.ProductionProcessPersistenceTests._insert_followup(conn, manual_id=self.manual_id)
            conn.execute("UPDATE production_followups SET batch_no='ORDER-1' WHERE id=?", (old,))
            order = self.order(conn)
        self.assertEqual(self.client.post(f'/admin/production-followups/orders/{order}/start').status_code, 302)
        with app.get_db() as conn:
            card = conn.execute('SELECT id FROM production_followups WHERE order_id=?', (order,)).fetchone()[0]
            step = conn.execute('SELECT id FROM production_followup_process_steps WHERE followup_id=?', (card,)).fetchone()[0]
        response = self.client.post(f'/admin/production-followups/{card}/processes/{step}/quantity',
            data={'quantity': '80', 'version': '9' * 30})
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT completed_quantity FROM production_followup_process_steps WHERE id=?', (step,)).fetchone()[0], 0)
