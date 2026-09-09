import json
import os
import re
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import app


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))

    def find(self, tag, **attrs):
        return [item for kind, item in self.items
                if kind == tag and all(key in item and item[key] == value for key, value in attrs.items())]


class BusinessListLayoutTests(unittest.TestCase):
    def setUp(self):
        self.original_db = app.DB_PATH
        self.original_ready = app.DATABASE_READY
        self.temp = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.temp.name) / 'test.db'
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        self.login('admin', 'admin')
        with app.get_db() as conn:
            now = '2026-09-04T10:00:00'
            manual = conn.execute("""INSERT INTO manuals
                (drawing_no, product_name, customer, model, category, version, remark,
                 filename, original_filename, created_at, updated_at)
                VALUES ('PART-A', '支架', '客户A', '', '', '', '', '', '', ?, ?)""", (now, now)).lastrowid
            self.order = conn.execute("""INSERT INTO product_orders
                (manual_id, order_no, ordered_at, quantity, customer, planned_ship_at, created_at, updated_at)
                VALUES (?, 'SO-100', '2026-09-01', 100, '客户A', '2026-09-03', ?, ?)""", (manual, now, now)).lastrowid
            self.shipment = conn.execute("""INSERT INTO product_order_shipments
                (order_id, shipped_quantity, shipped_at, created_at)
                VALUES (?, 20, '2026-09-04', ?)""", (self.order, now)).lastrowid
            self.plan = conn.execute("""INSERT INTO shipment_plans
                (plan_no, customer, planned_ship_at, created_at, updated_at)
                VALUES ('PLAN-100', '客户A', '2026-09-05', ?, ?)""", (now, now)).lastrowid
            conn.execute("""INSERT INTO shipment_plan_items
                (plan_id, order_id, planned_quantity, created_at, updated_at)
                VALUES (?, ?, 40, ?, ?)""", (self.plan, self.order, now, now))
            conn.execute("""INSERT INTO users
                (username, password_hash, role, active, can_manage_orders, can_view_orders,
                 can_manage_shipped, can_view_shipped, can_view_prices, created_at, updated_at)
                VALUES ('reader', 'hash', 'operator', 1, 0, 1, 0, 1, 0, ?, ?)""", (now, now))

    def login(self, username, role):
        with self.client.session_transaction() as session:
            session.update(admin_logged_in=True, admin_username=username, admin_role=role)

    def page(self, path, **query):
        response = self.client.get(path, query_string=query)
        self.assertEqual(response.status_code, 200)
        return Elements(response.get_data(as_text=True))

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.DATABASE_READY = self.original_ready
        self.temp.cleanup()

    def test_query_controls_have_visible_associated_labels_and_clear_navigation(self):
        for path, fields in [('/admin/orders', ['q', 'customer']),
                             ('/admin/shipped-orders', ['q', 'customer', 'shipped_at'])]:
            with self.subTest(path=path):
                page = self.page(path)
                labels = {attrs.get('for') for attrs in page.find('label')}
                for field in fields:
                    controls = page.find('input', name=field) + page.find('select', name=field)
                    control = next(c for c in controls if c.get('type') != 'hidden')
                    self.assertIsNotNone(control.get('id'), field)
                    self.assertIn(control['id'], labels)
                self.assertTrue(page.find('a', href=path))

    def test_scoped_styles_load_on_both_lists_but_not_other_pages(self):
        for path in ['/admin/orders', '/admin/shipped-orders', '/admin/shipped-orders/create']:
            page = self.page(path)
            links = [a['href'] for a in page.find('link', rel='stylesheet')
                     if urlsplit(a['href']).path == '/static/business-lists.css']
            self.assertEqual(len(links), 1)
            with self.client.get(links[0]) as response:
                self.assertEqual(response.status_code, 200)
        for path in ['/admin/products', '/admin/customers', '/admin/orders/new']:
            self.assertNotIn('/static/business-lists.css', self.client.get(path).get_data(as_text=True))

    def test_order_sort_filters_and_batch_form_associations_are_preserved(self):
        page = self.page('/admin/orders', q='SO-100', customer='客户A')
        for link in page.find('a'):
            if 'sort-link' in link.get('class', '').split():
                query = parse_qs(urlsplit(link['href']).query)
                self.assertEqual(query['q'], ['SO-100'])
                self.assertEqual(query['customer'], ['客户A'])
        checkbox = page.find('input', name='order_id', value=str(self.order))[0]
        self.assertEqual(checkbox['form'], 'shipment-plan-form')
        self.assertTrue(page.find('form', id='shipment-plan-form', method='post'))
        self.assertTrue(page.find('a', href='/admin/purchases/carton'))
        self.assertFalse(page.find('button', formaction='/admin/orders/carton-purchases'))
        self.assertTrue(any('due-overdue' in a.get('class', '') for a in page.find('td')))

    def test_shipping_history_keeps_export_but_removes_plan_and_create_panels(self):
        page = self.page('/admin/shipped-orders', q='SO-100', customer='客户A', shipped_at='2026-09-04')
        checkbox = page.find('input', name='shipment_id', value=str(self.shipment))[0]
        self.assertEqual(checkbox['form'], 'shipped-export-form')
        self.assertTrue(page.find('form', id='shipped-export-form', method='post'))
        excel = next(a for a in page.find('a') if 'format=xlsx' in a.get('href', ''))
        self.assertEqual(parse_qs(urlsplit(excel['href']).query),
                         {'format': ['xlsx'], 'q': ['SO-100'], 'customer': ['客户A'], 'shipped_at': ['2026-09-04']})
        self.assertFalse(page.find('input', name='planned_quantity'))
        self.assertFalse(page.find('form', **{'data-shipment-create-form': None}))
        self.assertFalse(page.find('form', **{'data-assembly-shipment-form': None}))
        for label in ['发货时间', '产品图号', '发货数量', '签收']:
            self.assertTrue(page.find('td', **{'data-label': label}), label)

    def test_shipping_operations_has_both_modes_without_history_or_plans(self):
        page = self.page('/admin/shipped-orders/create')
        self.assertTrue(page.find('form', **{'data-shipment-create-form': None}))
        self.assertTrue(page.find('form', **{'data-assembly-shipment-form': None}))
        self.assertTrue(page.find('option', value=str(self.order), **{'data-order-no': 'SO-100'}))
        self.assertFalse(page.find('form', id='shipped-export-form'))
        self.assertFalse(page.find('table', **{'class': 'shipped-order-table'}))
        self.assertFalse(page.find('input', name='planned_quantity'))
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT plan_no FROM shipment_plans WHERE id = ?', (self.plan,)).fetchone()[0], 'PLAN-100')
            self.assertEqual(conn.execute('SELECT planned_quantity FROM shipment_plan_items WHERE plan_id = ?', (self.plan,)).fetchone()[0], 40)

    def test_shipping_subpages_have_active_navigation_and_admin_entry(self):
        for route in ['/admin/shipped-orders/create', '/admin/shipped-orders']:
            page = self.page(route)
            self.assertTrue(page.find('nav', **{'aria-label': '发货子页面'}))
            self.assertTrue(page.find('a', href=route, **{'aria-current': 'page'}))
            self.assertTrue(page.find('a', href='/admin/shipped-orders/create'))
            self.assertTrue(page.find('a', href='/admin/shipped-orders'))
            main_nav = [a for a in page.find('a', href='/admin/shipped-orders/create')
                        if 'primary-nav-link' in a.get('class', '').split()]
            self.assertEqual(len(main_nav), 1)
            self.assertIn('active-nav-link', main_nav[0]['class'].split())

    def test_readonly_user_cannot_open_shipping_operations_or_see_its_tab(self):
        self.login('reader', 'operator')
        response = self.client.get('/admin/shipped-orders/create')
        self.assertEqual(response.status_code, 302)
        page = self.page('/admin/shipped-orders')
        self.assertFalse(page.find('a', href='/admin/shipped-orders/create'))
        self.assertTrue(page.find('a', href='/admin/shipped-orders', **{'aria-current': 'page'}))

    def test_failed_shipping_returns_to_operations_without_creating_a_record(self):
        for data in [{}, {'shipped_at': '2026-09-04'},
                     {'shipped_at': '2026-09-04', 'order_id': str(self.order), 'shipped_quantity': '81'},
                     {'shipped_at': '2026-09-04', 'order_id': str(self.order), 'shipped_quantity': '-1'}]:
            response = self.client.post('/admin/shipped-orders/new', data=data)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(urlsplit(response.location).path, '/admin/shipped-orders/create')
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_order_shipments').fetchone()[0], 1)

    def test_successful_shipping_opens_delivery_result_with_saved_record_in_history(self):
        with app.get_db() as conn:
            manual_id = conn.execute('SELECT manual_id FROM product_orders WHERE id = ?', (self.order,)).fetchone()[0]
            location = app.get_or_create_default_location(conn)
            conn.execute('INSERT INTO inventory_balances (manual_id, location_id, quantity, updated_at) VALUES (?, ?, 10, ?)',
                         (manual_id, location['id'], '2026-09-04T10:00:00'))
        response = self.client.post('/admin/shipped-orders/new', data={
            'shipped_at': '2026-09-04', 'order_id': str(self.order), 'shipped_quantity': '2',
        })
        self.assertEqual(response.status_code, 302)
        self.assertRegex(urlsplit(response.location).path, r'^/admin/delivery-notes/operations/\d+$')
        page = self.page(response.location)
        self.assertEqual(len(page.find('tr')), 2)
        self.assertTrue(any(a.get('href', '').endswith('.pdf') for a in page.find('a')))
        page = self.page('/admin/shipped-orders')
        self.assertEqual(len(page.find('input', name='shipment_id')), 2)
        self.assertFalse(page.find('form', **{'data-shipment-create-form': None}))

    def test_readonly_user_keeps_export_but_cannot_see_price_or_write_controls(self):
        admin = self.page('/admin/shipped-orders')
        self.assertTrue(admin.find('td', **{'data-label': '发货单价'}))
        self.login('reader', 'operator')
        orders = self.page('/admin/orders')
        self.assertFalse(orders.find('form', id='shipment-plan-form'))
        shipped = self.page('/admin/shipped-orders')
        self.assertTrue(shipped.find('form', id='shipped-export-form'))
        self.assertFalse(shipped.find('td', **{'data-label': '发货单价'}))
        self.assertFalse(shipped.find('input', name='unit_price'))
        self.assertFalse(any('data-shipment-create-form' in a or 'data-assembly-shipment-form' in a
                             for a in shipped.find('form')))

    def test_business_stylesheet_changes_cache_version(self):
        root = Path(self.temp.name) / 'assets'
        (root / 'static').mkdir(parents=True)
        for name in ['style.css', 'editor.js', 'business-lists.css']:
            asset = root / 'static' / name
            asset.write_text('/* test fixture */')
            os.utime(asset, (100, 100))
        with patch.object(app, 'BASE_DIR', root), app.app.test_request_context('/admin/orders'):
            self.assertEqual(app.upload_limits()['static_version'], 100)
            os.utime(root / 'static/business-lists.css', (200, 200))
            self.assertEqual(app.upload_limits()['static_version'], 200)

    def test_inline_list_scripts_parse_for_admin_and_readonly_users(self):
        for username, role in [('admin', 'admin'), ('reader', 'operator')]:
            self.login(username, role)
            routes = ['/admin/orders', '/admin/shipped-orders']
            if username == 'admin':
                routes.append('/admin/shipped-orders/create')
            for path in routes:
                with self.subTest(username=username, path=path):
                    html = self.client.get(path).get_data(as_text=True)
                    scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
                    self.assertTrue(scripts)
                    for script in scripts:
                        result = subprocess.run(['node', '--check'], input=script, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_long_order_remark_has_an_expandable_escaped_copy(self):
        remark = '按客户图纸加工，检验后独立包装。<script>unsafe()</script>'
        with app.get_db() as conn:
            conn.execute('UPDATE product_orders SET remark = ? WHERE id = ?', (remark, self.order))
        html = self.client.get('/admin/orders').get_data(as_text=True)
        page = Elements(html)
        self.assertTrue(page.find('details', **{'class': 'erp-text-details'}))
        self.assertIn('&lt;script&gt;unsafe()&lt;/script&gt;', html)
        self.assertNotIn('<script>unsafe()</script>', html)

    @unittest.skipUnless(os.environ.get('JIEDE_BROWSER_TESTS') == '1',
                         'Set JIEDE_BROWSER_TESTS=1 with Node.js/Playwright to test rendered colors')
    def test_shipping_high_contrast_colors_in_real_browser(self):
        # Real Flask output and shipped assets; all browser requests stay in memory.
        self.client.post('/admin/shipped-orders/new', data={})
        paths = ['/admin/shipped-orders', '/admin/shipped-orders/create', '/admin/orders', '/admin/customers',
                 '/static/style.css', '/static/business-lists.css', '/static/assembly_shipping.js',
                 '/static/pdf-preview.js']
        responses = {}
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            responses[path] = {'body': response.get_data(as_text=True), 'contentType': response.content_type}
        result = subprocess.run(
            ['node', str(Path(__file__).parent / 'shipping_contrast.cjs')],
            input=json.dumps(responses), text=True, capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
