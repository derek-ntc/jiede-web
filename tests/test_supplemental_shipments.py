"""Supplemental source persistence; allocation/UI behavior belongs to Task 6."""

import sqlite3

import app
import shipping_workflow as shipping
from tests import test_delivery_note_items as delivery_tests


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
