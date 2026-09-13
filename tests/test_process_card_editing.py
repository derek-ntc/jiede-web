import unittest
import app
import production_processes as processes
from tests import test_order_production as fixtures


class ProcessCardEditingTests(unittest.TestCase):
    setUp = fixtures.OrderProductionTests.setUp
    tearDown = fixtures.OrderProductionTests.tearDown
    _insert_manual = staticmethod(fixtures.OrderProductionTests._insert_manual)
    order = fixtures.OrderProductionTests.order
    service = fixtures.OrderProductionTests.service
    started = fixtures.OrderProductionTests.started

    def draft(self, card):
        with app.get_db() as conn:
            rows = processes.load_followup_process_card(conn, card)
        return rows, processes.process_card_revision(rows)

    def test_save_all_updates_names_quantities_and_remarks_preserving_step_ids(self):
        _, card, _ = self.started()
        rows, revision = self.draft(card)
        entries = [dict(id=r['id'], name=r['name'], quantity=20+i, remark=f'参数{i}') for i,r in enumerate(rows)]
        entries[0]['name'], entries[1]['name'] = entries[1]['name'], entries[0]['name']
        with app.get_db() as conn:
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
        actual, _ = self.draft(card)
        self.assertEqual([r['id'] for r in actual], [r['id'] for r in rows])
        self.assertEqual([r['name'] for r in actual], ['折弯','激光','焊接'])
        self.assertEqual([r['completed_quantity'] for r in actual], [20,21,22])
        self.assertEqual([r['remark'] for r in actual], ['参数0','参数1','参数2'])

    def test_invalid_row_or_stale_revision_cannot_partially_save(self):
        _, card, _ = self.started()
        rows, revision = self.draft(card)
        entries = [dict(id=r['id'], name=r['name'], quantity=30, remark='修改') for r in rows]
        entries[-1]['quantity'] = -1
        with app.get_db() as conn, self.assertRaises(ValueError):
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
        self.assertEqual(self.draft(card)[0], rows)
        entries[-1]['quantity'] = 30
        with app.get_db() as conn:
            processes.update_followup_process_step_remark(conn, card, rows[0]['id'], '其他人更新')
        with app.get_db() as conn, self.assertRaises(ValueError):
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
        self.assertEqual(self.draft(card)[0][0]['remark'], '其他人更新')

    def test_card_save_route_checks_csrf_and_ownership(self):
        order, card, _ = self.started()
        rows, revision = self.draft(card)
        data = {'card_revision': revision, 'return_order_id':str(order), 'step_id':[str(r['id']) for r in rows],
                'process_name':['切割','折弯','焊接'], 'process_quantity':['10','20','30'], 'process_remark':['A','B','C']}
        url = f'/admin/production-followups/{card}/processes/save'
        self.assertEqual(self.client.post(url, data=data, headers={'X-CSRF-Token':''}).status_code, 403)
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.draft(card)[0][0]['name'], '切割')
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn('保存本规格', page.text)
        self.assertNotIn('保存备注', page.text)
        self.assertNotIn('保存数量', page.text)

    def test_wrong_step_and_duplicate_names_leave_card_unchanged(self):
        _, card, _ = self.started()
        rows, revision = self.draft(card)
        entries = [dict(id=r['id'], name=r['name'], quantity=0, remark='') for r in rows]
        entries[0]['id'] = 999999
        with app.get_db() as conn, self.assertRaises(ValueError):
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
        entries[0]['id'] = rows[0]['id']
        entries[0]['name'] = entries[1]['name']
        with app.get_db() as conn, self.assertRaises(ValueError):
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
        self.assertEqual(self.draft(card)[0], rows)

    def test_rename_completed_step_preserves_completion_and_audit(self):
        _, card, step = self.started()
        with app.get_db() as conn:
            self.service().set_process_quantity(conn, card, step, 200, 0, 'admin', 'completed-time')
        rows, revision = self.draft(card)
        entries = [dict(id=r['id'], name=r['name'], quantity=r['completed_quantity'], remark=r['remark']) for r in rows]
        entries[0]['name'] = '切割'
        with app.get_db() as conn:
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
            audits = conn.execute('SELECT COUNT(*) FROM order_production_quantity_audit').fetchone()[0]
            legacy = conn.execute('SELECT laser_completed_at FROM production_followups WHERE id=?', (card,)).fetchone()[0]
        actual, _ = self.draft(card)
        self.assertEqual(actual[0]['completed_at'], 'completed-time')
        self.assertEqual(actual[0]['completed_quantity'], 200)
        self.assertEqual(audits, 1)
        self.assertEqual(legacy, '')

    def test_bulk_save_allows_overproduction_and_completion_keeps_actual_quantity(self):
        order, card, step = self.started(quantity=500)
        rows, revision = self.draft(card)
        data = {'card_revision': revision, 'step_id': [str(r['id']) for r in rows],
                'process_name': [r['name'] for r in rows], 'process_quantity': ['600','0','0'],
                'process_remark': ['超量生产','','']}
        response = self.client.post(f'/admin/production-followups/{card}/processes/save', data=data)
        self.assertEqual(response.status_code, 302)
        actual, _ = self.draft(card)
        self.assertEqual(actual[0]['completed_quantity'], 600)
        self.assertTrue(actual[0]['completed_at'])
        self.client.post(f'/admin/production-followups/{card}/processes/{step}/complete', data={'version': actual[0]['version']})
        self.assertEqual(self.draft(card)[0][0]['completed_quantity'], 600)
        with app.get_db() as conn:
            row = conn.execute('SELECT * FROM product_orders WHERE id=?', (order,)).fetchone()
            item = self.service().decorate_order_processes(conn, row)
            self.service().validate_order_production_change(conn, order, self.manual_id, 500)
        self.assertEqual(item['progress'], 33.3)
        self.assertEqual(item['processes'][0]['status'], '超额完成')
        page = self.client.get(response.location).text
        self.assertIn('600 / 500', page)
        self.assertNotIn('max="500"', page)

    def test_process_changes_follow_product_but_quantities_stay_with_order(self):
        order, card, step = self.started()
        _, existing_card, _ = self.started()
        rows, revision = self.draft(card)
        entries = [dict(id=r['id'], name=r['name'], quantity=10, remark='本单备注') for r in rows]
        entries[0]['name'] = '外协激光下料'
        with app.get_db() as conn:
            processes.save_followup_process_card(conn, card, entries, revision, 'admin')
            processes.move_followup_process_step(conn, card, rows[1]['id'], 'up')
            processes.add_followup_process_step(conn, card, '喷塑')
            processes.delete_followup_process_step(conn, card, rows[2]['id'], confirmed=True, operator='admin')
            template = processes.load_manual_process_template(conn, self.manual_id)
        self.assertEqual([r['name'] for r in template], ['折弯','外协激光下料','喷塑'])
        _, new_card, _ = self.started()
        fresh, _ = self.draft(new_card)
        self.assertEqual([r['name'] for r in fresh], [r['name'] for r in template])
        self.assertTrue(all(r['completed_quantity'] == 0 and r['remark'] == '' for r in fresh))
        self.assertEqual(self.draft(existing_card)[0][0]['name'], '激光')
        page = self.client.get(f'/admin/production-followups/orders/{order}').text
        self.assertNotIn('全部完成', page)
        self.assertNotIn('确认归零', page)
        self.assertIn('删除工艺', page)
