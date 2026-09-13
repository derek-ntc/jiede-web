import unittest
import app
from openpyxl import load_workbook
import procurement as p
import procurement_inventory as pi
from procurement_documents import build_purchase_order_workbook, purchase_document_view
from tests import test_purchase_orders as order_fixtures
from tests.test_purchase_orders import payload
from tests import test_purchase_receipts as receipt_fixtures
from tests.test_purchase_order_exports import sample_order


class RawMaterialOrderTests(unittest.TestCase):
    setUp = order_fixtures.PurchaseOrderDomainTests.setUp
    tearDown = order_fixtures.PurchaseOrderDomainTests.tearDown

    def rows(self):
        common = dict(material='Q235', quantity='2', expected_at='2026-10-01')
        return [dict(common, material_type='plate', length='2000', width='1000', thickness='3'),
                dict(common, material_type='square_tube', width='50', height='30', thickness='2', length='6000'),
                dict(common, material_type='round_tube', width='60', thickness='3')]

    def test_mixed_order_round_trips_and_exports_all_specifications(self):
        normalized = p.normalize_purchase_order_payload(payload('raw_material', self.rows()), can_view_prices=True)
        oid = p.create_purchase_order(self.conn, normalized, 'buyer', '2026-09-13')
        _, rows = p.load_purchase_order(self.conn, oid)
        self.assertEqual([dict(r).get('material_type') for r in rows], ['plate', 'square_tube', 'round_tube'])
        self.assertEqual([r['spec'] for r in rows], ['板 2000×1000×3 mm', '方管 50×30×2 mm；定尺 6000 mm', '圆管 Φ60×3 mm'])
        p.ensure_procurement_tables(self.conn)
        self.assertEqual(p.load_purchase_order(self.conn, oid)[1][1]['material_type'], 'square_tube')
        model = purchase_document_view(sample_order('raw_material'), rows, include_prices=False)
        self.assertEqual([r['spec'] for r in model['rows']], [r['spec'] for r in rows])
        sheet = load_workbook(build_purchase_order_workbook(sample_order('raw_material'), rows, include_prices=False)).active
        values = [c.value for row in sheet for c in row]
        for row in rows:
            self.assertIn(row['spec'], values)

    def test_required_dimensions_and_tube_wall_are_validated(self):
        for row in [dict(self.rows()[0], length=''), dict(self.rows()[1], height=''),
                    dict(self.rows()[2], width=''), dict(self.rows()[2], thickness='30'),
                    dict(self.rows()[1], thickness='15'), dict(self.rows()[0], material_type='unknown')]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                p.normalize_purchase_order_payload(payload('raw_material', [row]), can_view_prices=True)

    def test_switching_type_clears_unused_dimension_and_rebuilds_spec(self):
        row = dict(self.rows()[2], height='30', spec='旧规格')
        actual = p.normalize_purchase_order_payload(payload('raw_material', [row]), can_view_prices=False)['rows'][0]
        self.assertIsNone(actual['height'])
        self.assertEqual(actual['spec'], '圆管 Φ60×3 mm')


class RawMaterialReceiptTests(unittest.TestCase):
    setUp = receipt_fixtures.PurchaseReceiptTests.setUp
    tearDown = receipt_fixtures.PurchaseReceiptTests.tearDown
    create_order = receipt_fixtures.PurchaseReceiptTests.create_order
    scalar = receipt_fixtures.PurchaseReceiptTests.scalar
    call = receipt_fixtures.PurchaseReceiptTests.call
    preview = receipt_fixtures.PurchaseReceiptTests.preview
    payload = receipt_fixtures.PurchaseReceiptTests.payload
    post = receipt_fixtures.PurchaseReceiptTests.post

    def test_round_tube_without_cut_length_can_be_received_and_transferred(self):
        tube = dict(material_type='round_tube', material='304', width='60', thickness='3', quantity='2', expected_at='2026-10-01')
        oid, iid = self.create_order(rows=[tube])
        data = self.payload(oid=oid, item_id=iid, actual='2', qualified='2')
        data['rows'][0] = dict(tube, purchase_order_item_id=iid, actual_quantity='2', qualified_quantity='2', location_id='1', invoice_status='pending')
        receipt = self.post(data)
        lot = dict(self.conn.execute('SELECT * FROM purchase_inventory_lots WHERE receipt_id=?', (receipt['id'],)).fetchone())
        self.assertEqual((lot['material_type'], lot['spec'], lot['length']), ('round_tube', '圆管 Φ60×3 mm', None))
        _, rows = pi.load_purchase_receipt_document(self.conn, receipt['id'], include_prices=False)
        self.assertEqual(rows[0]['ordered']['material_type'], 'round_tube')
        self.assertEqual(rows[0]['spec'], '圆管 Φ60×3 mm')
        self.conn.execute("INSERT INTO warehouse_locations VALUES (2,'RAW-B','备用区',1)")
        result = pi.transfer_purchase_inventory(self.conn, lot['id'], 1, 2, '移库', 'keeper', self.now, 1, 'tube-transfer')
        moved = dict(self.conn.execute('SELECT * FROM purchase_inventory_lots WHERE id=?', (result['target_lot_id'],)).fetchone())
        self.assertEqual((moved['material_type'], moved['spec']), ('round_tube', '圆管 Φ60×3 mm'))


class RawMaterialRouteTests(unittest.TestCase):
    setUp = order_fixtures.PurchaseOrderRouteTests.setUp
    tearDown = order_fixtures.PurchaseOrderRouteTests.tearDown
    login = order_fixtures.PurchaseOrderRouteTests.login

    def test_form_save_edit_and_error_redisplay_preserve_dimensions(self):
        data = payload('raw_material', RawMaterialOrderTests().rows())
        form = order_fixtures.form(data)
        response = self.client.post('/admin/purchases/raw-material/new', data=form)
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.location)
        self.assertIn('圆管 Φ60×3 mm', detail.text)
        with app.get_db() as conn:
            oid = conn.execute('SELECT id FROM purchase_orders').fetchone()[0]
            rows = p.load_purchase_order(conn, oid)[1]
        edit_url = f'/admin/purchases/orders/{oid}/edit'
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        for item, row in zip(data['rows'], rows):
            item['id'] = row['id']
        data['rows'][1]['height'] = '35'
        response = self.client.post(edit_url, data=order_fixtures.form(data))
        self.assertEqual(response.status_code, 302)
        self.assertIn('方管 50×35×2 mm', self.client.get(response.location).text)
        data['rows'][2]['thickness'] = ''
        response = self.client.post(edit_url, data=order_fixtures.form(data))
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="35"', response.text)
        self.assertIn('value="round_tube" selected', response.text)
        with app.get_db() as conn:
            self.assertEqual(p.load_purchase_order(conn, oid)[1][2]['thickness'], 3)
