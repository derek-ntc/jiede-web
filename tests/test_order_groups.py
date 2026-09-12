import unittest

import app
from tests import test_order_production as fixtures


class OrderGroupTests(unittest.TestCase):
    setUp = fixtures.OrderProductionTests.setUp
    tearDown = fixtures.OrderProductionTests.tearDown
    _insert_manual = staticmethod(fixtures.OrderProductionTests._insert_manual)
    order = fixtures.OrderProductionTests.order

    def test_full_key_and_sets_not_summed(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('production_order_views'))
        from production_order_views import group_order_rows
        rows = [dict(id=i, customer=c, order_no='SO1', ordered_at='2026-09-12',
                     assembly_drawing_no='DZ30', assembly_set_quantity=100)
                for i, c in [(1, 'A'), (2, 'A'), (3, 'B')]]
        groups = group_order_rows(rows)
        self.assertEqual([g['line_count'] for g in groups], [2, 1])
        self.assertEqual(groups[0]['assembly_set_quantity'], 100)

    def test_keyword_keeps_entire_ordinary_group_and_anchor_resolved_server_side(self):
        with app.get_db() as conn:
            first = self.order(conn)
            self.manual_id = self._insert_manual(conn, 'P-OTHER', '另一产品')
            second = self.order(conn)
            conn.execute("UPDATE product_orders SET planned_ship_at='2026-10-01' WHERE id=?", (second,))
        page = self.client.get('/admin/orders?q=P-100').get_data(as_text=True)
        self.assertIn(f'/admin/orders/groups/{first}', page)
        detail = self.client.get(f'/admin/orders/groups/{first}?q=P-100&customer=伪造')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('P-OTHER', detail.get_data(as_text=True))
        self.assertIn('P-100', detail.get_data(as_text=True))
        self.assertIn('规格型号', detail.get_data(as_text=True))
        self.assertIn('规格-A', detail.get_data(as_text=True))
        self.assertEqual(self.client.get('/admin/orders/groups/99999').status_code, 404)

    def test_summary_sort_and_detail_sort_preserve_filter_context(self):
        with app.get_db() as conn:
            self.order(conn, 'ZZ')
            first = self.order(conn, 'AA')
            self.manual_id = self._insert_manual(conn, 'AAA-PART', 'first-sorted')
            self.order(conn, 'AA')
        for path in ['/admin/orders', '/admin/production-followups']:
            html = self.client.get(path, query_string={'sort': 'order_no', 'direction': 'asc'}).get_data(as_text=True)
            self.assertLess(html.index('<td>AA</td>'), html.index('<td>ZZ</td>'))
        html = self.client.get(f'/admin/orders/groups/{first}', query_string={'sort': 'drawing_no', 'direction': 'desc', 'q': 'P-100', 'customer': '客户A'}).get_data(as_text=True)
        self.assertLess(html.index('>P-100<'), html.index('>AAA-PART<'))
        self.assertIn(f'/admin/orders/{first}/edit?q=P-100', html)

    def test_key_boundary_and_dates_and_all_rows_shipped(self):
        from production_order_views import group_order_rows
        rows = [dict(id=i, customer='A', order_no='N', ordered_at=date,
            assembly_drawing_no=drawing, assembly_set_quantity=sets, planned_ship_at=due,
            unshipped_quantity=remaining)
            for i, date, drawing, sets, due, remaining in [
                (1, '2026-09-12', 'DZ', 10, '2026-09-13', 0),
                (2, '2026-09-12', 'DZ', 10, '2026-09-15', 1),
                (3, '2026-09-12', 'DZ', 20, '', 0),
                (4, '2026-09-12', 'DZ2', 10, '', 0),
                (5, '2026-09-11', 'DZ', 10, '', 0)]]
        groups = group_order_rows(rows)
        self.assertEqual(len(groups), 4)
        self.assertEqual((groups[0]['planned_ship_from'], groups[0]['planned_ship_to']), ('2026-09-13', '2026-09-15'))
        self.assertFalse(groups[0]['completed'])
        self.assertTrue(groups[1]['completed'])

    def test_detail_pages_keep_the_matching_main_navigation_active(self):
        from tests.test_business_list_layout import Elements
        with app.get_db() as conn:
            order = self.order(conn)
        for path, target in [(f'/admin/orders/groups/{order}', '/admin/orders'),
                             (f'/admin/production-followups/orders/{order}', '/admin/production-followups')]:
            links = Elements(self.client.get(path).get_data(as_text=True)).find('a', href=target)
            self.assertTrue(any('active-nav-link' in link.get('class', '') for link in links))

    def test_delete_anchor_returns_remaining_group_with_original_filters(self):
        from urllib.parse import parse_qs, urlsplit
        with app.get_db() as conn:
            first = self.order(conn)
            second = self.order(conn)
        response = self.client.post(f'/admin/orders/{first}/delete?q=P-100&sort=order_no&direction=asc')
        location = urlsplit(response.location)
        self.assertEqual(location.path, f'/admin/orders/groups/{second}')
        self.assertEqual(parse_qs(location.query), {'q': ['P-100'], 'sort': ['order_no'], 'direction': ['asc']})

    def test_rejected_edit_keeps_filter_return_context(self):
        from urllib.parse import parse_qs, urlsplit
        with app.get_db() as conn:
            order = self.order(conn)
        response = self.client.post(f'/admin/orders/{order}/edit?q=P-100', data={})
        self.assertEqual(parse_qs(urlsplit(response.location).query), {'q': ['P-100']})

    def test_edit_cancel_returns_to_group_with_all_filter_context(self):
        import re
        from html import unescape
        from urllib.parse import parse_qs, urlsplit
        with app.get_db() as conn:
            order = self.order(conn)
        response = self.client.get(f'/admin/orders/{order}/edit', query_string={
            'q': 'P-100', 'customer': '客户A', 'sort': 'order_no', 'direction': 'asc'})
        self.assertEqual(response.status_code, 200)
        links = {label: unescape(href) for href, label in re.findall(
            r'<a\b[^>]*href="([^"]+)"[^>]*>(返回订单明细|取消)</a>',
            response.get_data(as_text=True))}
        self.assertEqual(links['取消'], links['返回订单明细'])
        target = urlsplit(links['取消'])
        self.assertEqual(target.path, f'/admin/orders/groups/{order}')
        self.assertEqual(parse_qs(target.query), {
            'q': ['P-100'], 'customer': ['客户A'], 'sort': ['order_no'], 'direction': ['asc']})
