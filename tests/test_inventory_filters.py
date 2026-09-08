import tempfile
import unittest
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app
from openpyxl import load_workbook


class InventoryElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def find(self, tag, **attrs):
        return [item for kind, item in self.elements
                if kind == tag and all(item.get(key) == value for key, value in attrs.items())]


class InventoryCustomerFilterTests(unittest.TestCase):
    def setUp(self):
        self.original_db = app.DB_PATH
        self.original_ready = app.DATABASE_READY
        self.temp = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.temp.name) / 'inventory.db'
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session.update(admin_logged_in=True, admin_username='admin', admin_role='admin')
        with app.get_db() as conn:
            now = '2026-09-04T10:00:00'
            location = conn.execute("""INSERT INTO warehouse_locations
                (name, code, enabled, created_at, updated_at)
                VALUES ('测试仓', 'TEST', 1, ?, ?)""", (now, now)).lastrowid
            self.ids = []
            for code, name, customer, quantity in [
                ('A-1', '连接支架', '客户A', 2),
                ('A-2', '固定板', ' 客户A ', 0),
                ('B-1', '连接支架', '客户A分公司', 3),
                ('C-1', '定位销', '客户B', 10),
                ('D-1', '连接支架', '', 0),
                ('E-1', '连接支架', "客户'100%", 1),
            ]:
                manual = conn.execute("""INSERT INTO manuals
                    (drawing_no, product_name, customer, model, category, version, remark,
                     filename, original_filename, min_stock, created_at, updated_at)
                    VALUES (?, ?, ?, '', '', '', '', '', '', 5, ?, ?)""", (code, name, customer, now, now)).lastrowid
                self.ids.append(str(manual))
                conn.execute("""INSERT INTO inventory_balances
                    (manual_id, location_id, quantity, updated_at) VALUES (?, ?, ?, ?)""",
                    (manual, location, quantity, now))

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.DATABASE_READY = self.original_ready
        self.temp.cleanup()

    def page(self, **query):
        response = self.client.get('/admin/inventory', query_string=query)
        self.assertEqual(response.status_code, 200)
        return InventoryElements(response.get_data(as_text=True))

    def row_ids(self, **query):
        return {c['value'] for c in self.page(**query).find('input', name='manual_id', form='inventory-label-selection')}

    def test_customer_filter_matches_exact_trimmed_product_customer(self):
        self.assertEqual(self.row_ids(customer='客户A'), set(self.ids[:2]))
        self.assertEqual(self.row_ids(customer="客户'100%"), {self.ids[5]})
        self.assertEqual(self.row_ids(customer="' OR 1=1 --"), set())

    def test_customer_keyword_and_stock_status_combine(self):
        self.assertEqual(self.row_ids(customer='客户A', q='连接', status='positive'), {self.ids[0]})
        self.assertEqual(self.row_ids(customer='客户A', status='zero'), {self.ids[1]})
        self.assertEqual(self.row_ids(customer='客户A', status='low'), set(self.ids[:2]))
        self.assertEqual(self.row_ids(customer='客户B', status='low'), set())

    def test_empty_customer_retains_all_products_including_unassigned(self):
        self.assertEqual(self.row_ids(customer=''), set(self.ids))

    def test_customer_selector_keeps_all_options_and_selected_filter(self):
        page = self.page(customer='客户A', q='连接', status='positive')
        selects = page.find('select', name='customer')
        self.assertEqual(len(selects), 1)
        self.assertTrue(page.find('label', **{'for': selects[0]['id']}))
        options = page.find('option')
        for customer in ['客户A', '客户A分公司', '客户B', "客户'100%"]:
            self.assertEqual(len([o for o in options if o.get('value') == customer]), 1)
        self.assertIn('selected', next(o for o in options if o.get('value') == '客户A'))
        export = next(a['href'] for a in page.find('a') if urlsplit(a.get('href', '')).path == '/admin/inventory/export.xlsx')
        self.assertEqual(parse_qs(urlsplit(export).query), {'customer': ['客户A'], 'q': ['连接'], 'status': ['positive']})
        self.assertTrue(page.find('a', href='/admin/inventory'))

    def test_excel_contains_only_products_matching_combined_filters(self):
        response = self.client.get('/admin/inventory/export.xlsx', query_string={
            'customer': '客户A', 'q': '连接', 'status': 'positive',
        })
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.data), read_only=True)
        try:
            rows = list(workbook.active.values)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][0:2], ('连接支架', 'A-1'))
            self.assertEqual(rows[1][4], 2)
        finally:
            workbook.close()


if __name__ == '__main__':
    unittest.main()
