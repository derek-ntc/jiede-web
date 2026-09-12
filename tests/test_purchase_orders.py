import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.datastructures import MultiDict

import app
import procurement as p
import procurement_inventory as pi


NOW = "2026-09-09T10:00:00"


def seed(conn):
    conn.execute("INSERT INTO suppliers (code,name,address,created_at,updated_at) VALUES ('SUP-1','供方甲','旧地址',?,?)", (NOW, NOW))
    conn.execute("INSERT INTO purchase_delivery_profiles (name,delivery_address,recipient,phone,default_remark,is_default,created_at,updated_at) VALUES ('公司','厂区地址','王五','123','工作日',1,?,?)", (NOW, NOW))
    conn.commit()


def payload(category="other", rows=None, **kw):
    result = dict(category=category, supplier_id="1", delivery_profile_id="1", purchased_at="2026-09-09", status="ordered",
                  rows=rows if rows is not None else [dict(item_name="螺栓", material="AB", quantity="12", unit_price="3.25", expected_at="2026-09-20")])
    result.update(kw)
    return result


def form(data):
    result = {k: v for k, v in data.items() if k != "rows"}
    for i, row in enumerate(data["rows"]):
        result.update({f"items[{i}][{key}]": str(value) for key, value in row.items()})
    return result


class PurchaseOrderDomainTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        p.ensure_procurement_tables(self.conn)
        seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def create(self, data=None, prices=True):
        normalized = p.normalize_purchase_order_payload(data or payload(), can_view_prices=prices)
        return p.create_purchase_order(self.conn, normalized, "buyer", NOW)

    def update(self, oid, data, prices=True):
        p.update_purchase_order(self.conn, oid, p.normalize_purchase_order_payload(data, can_view_prices=prices), "editor", NOW)

    def test_four_categories_and_server_totals(self):
        for category in ("raw_material", "carton", "outsourcing", "other"):
            with self.subTest(category=category):
                data = payload(category)
                data["rows"][0]["line_total_minor"] = "1"
                oid = self.create(data)
                order, rows = p.load_purchase_order(self.conn, oid)
                self.assertEqual(order["category"], category)
                self.assertEqual(order["currency"], "CNY")
                self.assertEqual((rows[0]["unit_price_minor"], rows[0]["line_total_minor"]), (325, 3900))
        self.assertEqual(self.conn.execute("SELECT COUNT(DISTINCT order_no) FROM purchase_orders").fetchone()[0], 4)

    def test_category_required_fields_and_optional_blanks(self):
        for category, row in (("raw_material", dict(material="钢")), ("carton", dict(material="AB")),
                              ("outsourcing", dict(drawing_no="D-1")), ("other", dict(item_name="丝攻"))):
            valid = dict(row, quantity="1", expected_at="2026-09-20")
            oid = self.create(payload(category, [valid]))
            self.assertEqual(p.load_purchase_order(self.conn, oid)[1][0]["surface"], "")
            with self.assertRaises(ValueError):
                self.create(payload(category, [dict(quantity="1", expected_at="2026-09-20")]))

    def test_snapshot_survives_master_edits_and_order_edit(self):
        oid = self.create()
        self.conn.execute("UPDATE suppliers SET address='新地址'")
        self.conn.execute("UPDATE purchase_delivery_profiles SET delivery_address='新厂区'")
        old, items = p.load_purchase_order(self.conn, oid)
        data = payload(rows=[dict(item_name="螺栓", quantity="13", unit_price="3.25", expected_at="2026-09-20", id=items[0]["id"])])
        data.pop("delivery_profile_id")
        self.update(oid, data)
        order, _ = p.load_purchase_order(self.conn, oid)
        self.assertEqual((order["supplier_address"], order["delivery_address"]), ("旧地址", "厂区地址"))
        self.assertEqual(old["recipient"], "王五")

    def test_explicit_delivery_edits_including_optional_blank(self):
        oid = self.create(payload(delivery_address="码头", recipient="李四", recipient_phone="456", remark=""))
        order, _ = p.load_purchase_order(self.conn, oid)
        self.assertEqual((order["delivery_address"], order["recipient"], order["remark"]), ("码头", "李四", ""))

    def test_inactive_or_missing_masters_rejected(self):
        for field in ("supplier_id", "delivery_profile_id"):
            for value in ("999", "no", "-1"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.create(payload(**{field: value}))
        for table in ("suppliers", "purchase_delivery_profiles"):
            self.conn.execute(f"UPDATE {table} SET active=0")
            with self.assertRaises(ValueError):
                self.create()
            self.conn.execute(f"UPDATE {table} SET active=1")

    def test_invalid_dates_quantities_dimensions_prices_and_status(self):
        for field, values in {"quantity": ("0", "-1", "1.5", "2147483648"), "expected_at": ("", "2026-02-30", "20260920"),
                              "length": ("NaN", "Infinity", "-2", "0", "1e999"), "unit_price": ("-1", "NaN", "3.251")}.items():
            for value in values:
                data = payload()
                data["rows"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.create(data)
        for kw in (dict(category="invalid"), dict(purchased_at="2026-02-30"), dict(purchased_at="20260909"), dict(rows=[]), dict(status="received"), dict(status="cancelled")):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.create(payload(**kw))

    def test_price_denied_create_and_edit_are_protected_by_item_id(self):
        hidden = self.create(prices=False)
        self.assertIsNone(p.load_purchase_order(self.conn, hidden)[1][0]["unit_price_minor"])
        oid = self.create(payload(rows=[dict(item_name="A", quantity="12", unit_price="3.25", expected_at="2026-09-20"), dict(item_name="B", quantity="2", unit_price="8.50", expected_at="2026-09-20")]))
        _, old = p.load_purchase_order(self.conn, oid)
        data = payload(rows=[dict(id=old[1]["id"], item_name="B", quantity="3", unit_price="0", expected_at="2026-09-20"), dict(id=old[0]["id"], item_name="A", quantity="4", expected_at="2026-09-20"), dict(item_name="新行", quantity="1", unit_price="99", expected_at="2026-09-20")])
        self.update(oid, data, prices=False)
        _, rows = p.load_purchase_order(self.conn, oid)
        self.assertEqual([(r["unit_price_minor"], r["line_total_minor"]) for r in rows], [(850, 2550), (325, 1300), (None, None)])
        self.assertEqual([r["id"] for r in rows[:2]], [old[1]["id"], old[0]["id"]])

    def test_foreign_duplicate_and_create_item_ids_rejected(self):
        first, second = self.create(), self.create()
        item = p.load_purchase_order(self.conn, first)[1][0]
        row = dict(id=item["id"], item_name="X", quantity="1", expected_at="2026-09-20")
        for oid, rows in ((second, [row]), (first, [row, row])):
            with self.assertRaises(ValueError):
                self.update(oid, payload(rows=rows))
        with self.assertRaises(ValueError):
            self.create(payload(rows=[row]))

    def test_receipt_cumulative_quantity_and_removal_guards(self):
        oid = self.create()
        item = p.load_purchase_order(self.conn, oid)[1][0]
        self.conn.execute("CREATE TABLE purchase_receipts (id INTEGER PRIMARY KEY, status TEXT)")
        self.conn.execute("CREATE TABLE purchase_receipt_items (id INTEGER PRIMARY KEY, receipt_id INTEGER, purchase_order_item_id INTEGER, actual_quantity INTEGER)")
        self.conn.execute("INSERT INTO purchase_receipts VALUES (1,'posted')")
        self.conn.executemany("INSERT INTO purchase_receipt_items VALUES (?,1,?,?)", [(1, item["id"], 3), (2, item["id"], 4)])
        self.conn.execute("UPDATE purchase_orders SET status='partially_received' WHERE id=?", (oid,))
        row = dict(id=item["id"], item_name="X", quantity="6", expected_at="2026-09-20")
        with self.assertRaises(ValueError):
            self.update(oid, payload(rows=[row]))
        with self.assertRaises(ValueError):
            self.update(oid, payload())
        row["quantity"] = "7"
        self.update(oid, payload(rows=[row]))
        self.assertEqual(p.load_purchase_order(self.conn, oid)[0]["status"], "partially_received")

    def test_immutable_orders_and_category_changes_rejected(self):
        for status in ("received", "cancelled"):
            oid = self.create()
            self.conn.execute("UPDATE purchase_orders SET status=? WHERE id=?", (status, oid))
            with self.assertRaises(ValueError):
                self.update(oid, payload())
        with self.assertRaises(ValueError):
            self.update(self.create(), payload("carton"))

    def test_voided_receipts_allow_quantity_edits_but_preserve_line_history(self):
        oid = self.create()
        item_id = p.load_purchase_order(self.conn, oid)[1][0]["id"]
        self.conn.execute("CREATE TABLE purchase_receipts (id INTEGER PRIMARY KEY, status TEXT)")
        self.conn.execute("CREATE TABLE purchase_receipt_items (id INTEGER PRIMARY KEY, receipt_id INTEGER, purchase_order_item_id INTEGER, actual_quantity INTEGER)")
        self.conn.executemany("INSERT INTO purchase_receipts VALUES (?,?)", [(1, "posted"), (2, "voided")])
        self.conn.executemany("INSERT INTO purchase_receipt_items VALUES (?,?,?,?)", [(1, 1, item_id, 3), (2, 2, item_id, 9)])
        row = dict(id=item_id, item_name="螺栓", quantity="3", expected_at="2026-09-20")
        self.update(oid, payload(rows=[row]))
        self.assertEqual(p.load_purchase_order(self.conn, oid)[1][0]["ordered_quantity"], 3)
        with self.assertRaises(ValueError):
            self.update(oid, payload())
        self.conn.execute("UPDATE purchase_receipts SET status='voided' WHERE id=1")
        row["quantity"] = "1"
        self.update(oid, payload(rows=[row]))
        before = tuple(self.conn.iterdump())
        with self.assertRaisesRegex(ValueError, "历史.*不能删除"):
            self.update(oid, payload())
        self.assertEqual(tuple(self.conn.iterdump()), before)

    def test_voided_receipt_line_removal_is_validation_error_without_partial_writes(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE warehouse_locations (id INTEGER PRIMARY KEY, code TEXT, name TEXT, enabled INTEGER)")
        self.conn.execute("INSERT INTO warehouse_locations VALUES (1,'RAW-A','原料区',1)")
        pi.ensure_purchase_inventory_tables(self.conn)
        for qualified in (3, 0):
            with self.subTest(qualified=qualified):
                rows = [dict(item_name=name, quantity="12", expected_at="2026-09-20")
                        for name in ("A", "B")]
                oid = self.create(payload(rows=rows))
                _, items = p.load_purchase_order(self.conn, oid)
                receipt = pi.post_purchase_receipt(self.conn, dict(
                    preview_token=pi.receipt_preview_token(pi.load_receipt_preview(self.conn, oid)),
                    idempotency_key=f"void-history-{qualified}", received_at="2026-09-12",
                    rows=[dict(purchase_order_item_id=items[0]["id"], item_name="实际A",
                               actual_quantity="6", qualified_quantity=str(qualified),
                               location_id="1", invoice_status="pending")]), "receiver", NOW)
                posted = pi.load_receipt_preview(self.conn, oid)
                self.assertEqual(posted["order"]["status"], "partially_received")
                self.assertEqual((posted["rows"][0]["actual_quantity"],
                                  posted["rows"][0]["qualified_quantity"]), (6, qualified))
                pi.void_purchase_receipt(self.conn, receipt["id"], "receiver", NOW)
                voided = pi.load_receipt_preview(self.conn, oid)
                self.assertEqual(voided["order"]["status"], "ordered")
                self.assertEqual([(r["actual_quantity"], r["qualified_quantity"], r["remaining_quantity"])
                                  for r in voided["rows"]], [(0, 0, 12), (0, 0, 12)])
                self.assertEqual(self.conn.execute(
                    "SELECT COUNT(*) FROM purchase_inventory_lots WHERE receipt_id=?",
                    (receipt["id"],)).fetchone()[0], 1 if qualified else 0)
                for row, item in zip(rows, items):
                    row["id"] = item["id"]
                rows[0]["quantity"] = "2"
                self.update(oid, payload(rows=rows))
                edited = pi.load_receipt_preview(self.conn, oid)
                self.assertEqual(edited["order"]["status"], "ordered")
                self.assertEqual(edited["rows"][0]["remaining_quantity"], 2)
                self.conn.commit()
                before = tuple(self.conn.iterdump())
                with self.assertRaisesRegex(ValueError, "历史.*不能删除"):
                    self.update(oid, payload(rows=[dict(rows[1], quantity="7")], remark="must rollback"))
                self.assertEqual(tuple(self.conn.iterdump()), before)
                self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_inventory_lot_dependency_also_protects_order_line_removal(self):
        oid = self.create()
        item_id = p.load_purchase_order(self.conn, oid)[1][0]["id"]
        self.conn.execute("CREATE TABLE purchase_inventory_lots (id INTEGER PRIMARY KEY, purchase_order_item_id INTEGER)")
        self.conn.execute("INSERT INTO purchase_inventory_lots VALUES (1,?)", (item_id,))
        before = tuple(self.conn.iterdump())
        with self.assertRaisesRegex(ValueError, "历史.*不能删除"):
            self.update(oid, payload(remark="must rollback"))
        self.assertEqual(tuple(self.conn.iterdump()), before)

    def test_transaction_rolls_back_header_and_earlier_lines_on_sql_failure(self):
        self.conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON purchase_order_items WHEN NEW.item_name='FAIL' BEGIN SELECT RAISE(ABORT,'injected'); END")
        data = payload(rows=[payload()["rows"][0], dict(item_name="FAIL", quantity="1", expected_at="2026-09-20")])
        with self.assertRaises(sqlite3.IntegrityError):
            self.create(data)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)
        oid = self.create()
        old_order, old_items = p.load_purchase_order(self.conn, oid)
        data["remark"] = "must rollback"
        data["rows"][0]["id"] = old_items[0]["id"]
        data["rows"][0]["quantity"] = "15"
        with self.assertRaises(sqlite3.IntegrityError):
            self.update(oid, data)
        order, items = p.load_purchase_order(self.conn, oid)
        self.assertEqual(dict(order), dict(old_order))
        self.assertEqual([dict(r) for r in items], [dict(r) for r in old_items])

    def test_filters_are_scoped_parameterized_and_cover_dates_and_keywords(self):
        oid = self.create()
        self.create(payload("carton"))
        self.assertEqual([r["id"] for r in p.fetch_purchase_orders(self.conn, "other", dict(supplier_id="1", q="螺栓", purchased_from="2026-09-01", purchased_to="2026-09-30", expected_from="2026-09-19", expected_to="2026-09-21", status="ordered", order_no="20260909"))], [oid])
        for filters in (dict(q="' OR 1=1 --"), dict(order_no="' OR 1=1 --"), dict(supplier_id="1 OR 1=1"), dict(expected_from="2026-09-21"), dict(purchased_to="2026-09-01"), dict(status="draft")):
            self.assertEqual(p.fetch_purchase_orders(self.conn, "other", filters), [])

    def test_form_normalization_preserves_posted_order_and_id(self):
        data = MultiDict(form(payload(rows=[dict(id="20", item_name="B", quantity="2", expected_at="2026-09-20"), dict(id="10", item_name="A", quantity="1", expected_at="2026-09-20")])))
        normalized = p.normalize_purchase_order_payload(data, can_view_prices=False)
        self.assertEqual([r["id"] for r in normalized["rows"]], [20, 10])


class PurchaseOrderRouteTests(unittest.TestCase):
    def setUp(self):
        self.original = app.DB_PATH, app.DATABASE_READY, app.app.config["TESTING"], app.app.config["SECRET_KEY"]
        self.tmp = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmp.name) / "test.db"
        app.DATABASE_READY = False
        app.app.config.update(TESTING=True, SECRET_KEY="purchase-test")
        app.init_db()
        with app.get_db() as conn:
            seed(conn)
            for name, view, manage, price in (("buyer", 1, 1, 1), ("blind", 1, 1, 0), ("reader", 1, 0, 0), ("outsider", 0, 0, 0)):
                conn.execute("INSERT INTO users (username,password_hash,role,active,can_view_purchases,can_manage_purchases,can_view_purchase_prices,created_at,updated_at) VALUES (?,'hash','operator',1,?,?,?,?,?)", (name, view, manage, price, NOW, NOW))
        self.client = app.app.test_client()
        self.login("buyer")

    def tearDown(self):
        app.DB_PATH, app.DATABASE_READY, testing, secret = self.original
        app.app.config.update(TESTING=testing, SECRET_KEY=secret)
        self.tmp.cleanup()

    def login(self, name):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(admin_logged_in=True, admin_username=name, admin_role="operator")

    def create(self, data=None):
        response = self.client.post("/admin/purchases/other/new", data=form(data or payload()))
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            row = conn.execute("SELECT id FROM purchase_orders ORDER BY id DESC").fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def test_four_pages_and_default_delivery_initial_row(self):
        for slug in ("raw-material", "carton", "outsourcing", "other"):
            self.assertEqual(self.client.get(f"/admin/purchases/{slug}").status_code, 200)
            response = self.client.get(f"/admin/purchases/{slug}/new")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('value="厂区地址"', html)
            self.assertIn('name="items[0][quantity]"', html)
            self.assertIn('value="1" selected', html)

    def test_route_category_is_authoritative_and_invalid_urls_are_404(self):
        data = form(payload("other"))
        self.assertEqual(self.client.post("/admin/purchases/raw-material/new", data=data).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT category FROM purchase_orders").fetchone()[0], "raw_material")
        for url in ("/admin/purchases/raw_material", "/admin/purchases/nonsense", "/admin/purchases/no/new", "/admin/purchases/orders/999", "/admin/purchases/orders/999/edit", "/admin/purchases/orders/no"):
            self.assertEqual(self.client.get(url).status_code, 404)
        oid = self.create()
        self.assertEqual(self.client.get(f"/admin/purchases/carton/{oid}").status_code, 404)

    def test_permission_gates_for_read_write_and_cancel(self):
        oid = self.create()
        self.login("reader")
        for url in ("/admin/purchases/other/new", f"/admin/purchases/orders/{oid}/edit"):
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url, data=form(payload())).status_code, 302)
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/cancel").status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purchase_orders WHERE id=?", (oid,)).fetchone()[0], "ordered")
        self.login("outsider")
        for url in ("/admin/purchases/other", f"/admin/purchases/orders/{oid}"):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_no_price_html_and_forged_create_edit(self):
        oid = self.create(payload(rows=[dict(item_name="秘密价格", quantity="12", unit_price="9876.54", expected_at="2026-09-20")]))
        self.login("blind")
        for url in ("/admin/purchases/other", "/admin/purchases/other/new", f"/admin/purchases/orders/{oid}", f"/admin/purchases/orders/{oid}/edit"):
            html = self.client.get(url).get_data(as_text=True)
            for forbidden in ("9876.54", "118518.48", "unit_price", "line_total", "order_total"):
                self.assertNotIn(forbidden, html)
        hidden = self.create()
        with app.get_db() as conn:
            self.assertIsNone(p.load_purchase_order(conn, hidden)[1][0]["unit_price_minor"])
            item_id = p.load_purchase_order(conn, oid)[1][0]["id"]
        data = payload(rows=[dict(id=item_id, item_name="秘密价格", quantity="2", unit_price="0", expected_at="2026-09-20")])
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/edit", data=form(data)).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(p.load_purchase_order(conn, oid)[1][0]["line_total_minor"], 1975308)

    def test_invalid_post_is_400_and_does_not_write(self):
        for data in (payload(supplier_id="999"), payload(delivery_profile_id="999"), payload(status="received"), payload(rows=[])):
            self.assertEqual(self.client.post("/admin/purchases/other/new", data=form(data)).status_code, 400)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)

    def test_validation_error_retains_entered_rows_without_leaking_forged_prices(self):
        self.login("blind")
        data = payload(remark="保留我的输入", rows=[dict(item_name="第一行保留", quantity="2", unit_price="54321.99", expected_at="2026-09-20"), dict(item_name="第二行保留", quantity="0", expected_at="2026-09-20")])
        response = self.client.post("/admin/purchases/other/new", data=form(data))
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn('value="第一行保留"', html)
        self.assertIn('value="第二行保留"', html)
        self.assertIn('>保留我的输入</textarea>', html)
        self.assertNotIn("54321.99", html)
        self.assertNotIn("unit_price", html)

    def test_category_mismatches_missing_ids_and_received_are_never_mutated(self):
        oid = self.create()
        for action in ("edit", "cancel"):
            self.assertEqual(self.client.post(f"/admin/purchases/carton/{oid}/{action}", data=form(payload())).status_code, 404)
            self.assertEqual(self.client.post(f"/admin/purchases/orders/999/{action}", data=form(payload())).status_code, 404)
        with app.get_db() as conn:
            conn.execute("UPDATE purchase_orders SET status='received' WHERE id=?", (oid,))
        for action in ("edit", "cancel"):
            self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/{action}", data=form(payload())).status_code, 400)

    def test_inactive_supplier_and_profile_post_rejected(self):
        for table in ("suppliers", "purchase_delivery_profiles"):
            with app.get_db() as conn:
                conn.execute(f"UPDATE {table} SET active=0")
            self.assertEqual(self.client.post("/admin/purchases/other/new", data=form(payload())).status_code, 400)
            with app.get_db() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)
                conn.execute(f"UPDATE {table} SET active=1")

    def test_draft_ordered_cancel_and_immutable_routes(self):
        oid = self.create(payload(status="draft"))
        with app.get_db() as conn:
            item_id = p.load_purchase_order(conn, oid)[1][0]["id"]
        data = payload()
        data["rows"][0]["id"] = item_id
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/edit", data=form(data)).status_code, 302)
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/cancel").status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(p.load_purchase_order(conn, oid)[0]["status"], "cancelled")
            self.assertEqual(len(p.load_purchase_order(conn, oid)[1]), 1)
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/edit", data=form(data)).status_code, 400)
        self.assertEqual(self.client.post(f"/admin/purchases/orders/{oid}/cancel").status_code, 400)

    def test_price_projection_totals_filters_and_escaped_text(self):
        oid = self.create(payload(rows=[dict(item_name="<script>alert(1)</script>", quantity="12", unit_price="3.25", expected_at="2026-09-20")]))
        html = self.client.get(f"/admin/purchases/orders/{oid}").get_data(as_text=True)
        self.assertIn("39.00", html)
        self.assertIn("RMB／人民币", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        html = self.client.get("/admin/purchases/other?q=nonexistent").get_data(as_text=True)
        self.assertNotIn("PO-20260909-0001", html)


if __name__ == "__main__":
    unittest.main()
