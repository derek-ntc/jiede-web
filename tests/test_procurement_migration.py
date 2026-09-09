"""Migration fixtures use only memory databases or explicitly temporary paths."""

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import procurement as p


NOW = "2026-09-09T10:00:00"
ROOT = Path(__file__).resolve().parents[1]
SUPPLIER_SOURCES = (
    "carton_suppliers", "arrival_suppliers", "powder_coating_suppliers",
    "carton_products", "carton_purchases", "arrival_records",
    "powder_coating_products", "powder_coating_records", "purchase_followups",
)


def schema(conn):
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    p.ensure_procurement_tables(conn)
    app.ensure_purchase_followup_table(conn)
    app.ensure_carton_purchase_table(conn)
    app.ensure_arrival_record_tables(conn)
    app.ensure_powder_coating_tables(conn)


def insert(conn, table, **values):
    values = dict(created_at=NOW, updated_at=NOW, **values)
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def fixture(conn):
    insert(conn, "suppliers", code="EXISTING", name="同名厂", contact="已有联系人", active=0)
    insert(conn, "carton_suppliers", id=1, name=" 同名厂 ", contact="纸箱联系人", phone="123", remark="纸箱备注")
    insert(conn, "arrival_suppliers", id=1, name="同名厂", contact="到货联系人", phone="456", remark="到货备注")
    insert(conn, "powder_coating_suppliers", id=1, name="同名厂", contact="喷塑联系人", phone="789", remark="喷塑备注")
    insert(conn, "carton_suppliers", id=2, name="Alpha", contact="首选")
    insert(conn, "arrival_suppliers", id=2, name="Alpha", contact="后选")
    insert(conn, "powder_coating_suppliers", id=2, name="alpha")
    insert(conn, "carton_products", id=3, print_mark="模板", supplier_name="同名厂", remark="模板备注")
    insert(conn, "carton_purchases", id=7, ordered_at="2020-02-03", print_mark="唛头 A", supplier_name="同名厂", board_type="AB", quantity=12, unit_price=3.25, carton_size="40×30×20", carton_length=40, carton_width=30, carton_height=20, received_at="2020-02-08", completed=1, remark="历史要求", recorded_by="旧采购员")
    insert(conn, "carton_purchases", id=8, ordered_at="2020-02-04", print_mark="唛头 B", quantity=2, unit_price=0.1, completed=1)
    insert(conn, "purchase_followups", id=8, recorded_at="2019-05-06", item_name="工具", quantity=4, image_filename="kept.png", image_original_filename="原图.png", remark="保留备注", recorded_by="旧员")
    insert(conn, "purchase_followups", id=9, recorded_at="2019-05-06", purchased_at="2019-05-07", item_name="量具", quantity=3)
    insert(conn, "arrival_records", id=11, arrived_at="2020-03-04", item_name="原料", quantity=5, unit_price=2.01, supplier_name="自由文本厂")
    insert(conn, "powder_coating_products", id=12, product_name="支架", supplier_name=" 自由文本厂 ")
    insert(conn, "powder_coating_records", id=13, product_name="支架", delivered_at="2020-03-05", quantity=6)
    conn.commit()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        schema(self.conn)
        fixture(self.conn)

    def tearDown(self):
        self.conn.close()

    def migrate(self):
        self.assertTrue(callable(getattr(p, "migrate_legacy_procurement", None)), "migration API is missing")
        return p.migrate_legacy_procurement(self.conn, NOW)

    def report(self):
        self.assertTrue(callable(getattr(p, "procurement_migration_report", None)), "read-only report API is missing")
        return p.procurement_migration_report(self.conn)

    def test_exact_name_precedence_conflicts_and_all_source_links(self):
        self.migrate()
        suppliers = {r["name"]: dict(r) for r in self.conn.execute("SELECT * FROM suppliers")}
        kept = suppliers["同名厂"]
        self.assertEqual((kept["code"], kept["contact"], kept["phone"], kept["remark"], kept["active"]), ("EXISTING", "已有联系人", "123", "纸箱备注", 0))
        self.assertEqual((kept["address"], kept["email"]), ("", ""))
        self.assertNotEqual(suppliers["Alpha"]["id"], suppliers["alpha"]["id"])
        self.assertEqual(suppliers["Alpha"]["contact"], "首选")
        self.assertEqual(suppliers["历史未指定供应商"]["active"], 0)
        for source in SUPPLIER_SOURCES:
            old = {r[0] for r in self.conn.execute(f"SELECT id FROM {source}")}
            linked = {r[0] for r in self.conn.execute("SELECT legacy_id FROM supplier_legacy_links WHERE legacy_source=?", (source,))}
            self.assertEqual(old, linked, source)
        conflict = self.conn.execute("SELECT * FROM procurement_migration_conflicts WHERE legacy_source='arrival_suppliers' AND legacy_id=1 AND field='phone'").fetchone()
        self.assertEqual((conflict["kept_value"], conflict["incoming_value"], conflict["entity"]), ("123", "456", "supplier"))

    def test_idempotence_stable_source_keys_snapshots_and_attachments(self):
        old = {s: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {s} ORDER BY id")] for s in SUPPLIER_SOURCES}
        self.assertEqual(self.migrate()["orders_created"], 4)
        orders = [dict(r) for r in self.conn.execute("SELECT * FROM purchase_orders ORDER BY id")]
        items = [dict(r) for r in self.conn.execute("SELECT * FROM purchase_order_items ORDER BY id")]
        conflicts = [tuple(r) for r in self.conn.execute("SELECT * FROM procurement_migration_conflicts ORDER BY id")]
        self.assertEqual(self.migrate()["orders_created"], 0)
        self.assertEqual(orders, [dict(r) for r in self.conn.execute("SELECT * FROM purchase_orders ORDER BY id")])
        self.assertEqual(items, [dict(r) for r in self.conn.execute("SELECT * FROM purchase_order_items ORDER BY id")])
        self.assertEqual(conflicts, [tuple(r) for r in self.conn.execute("SELECT * FROM procurement_migration_conflicts ORDER BY id")])
        self.assertEqual([r["order_no"] for r in orders], ["LEGACY-CARTON-7", "LEGACY-CARTON-8", "LEGACY-OTHER-8", "LEGACY-OTHER-9"])
        self.assertEqual([(r["legacy_source"], r["legacy_id"]) for r in orders], [(r["legacy_source"], r["legacy_id"]) for r in items])
        first, item = orders[0], items[0]
        self.assertEqual((first["supplier_name"], first["supplier_contact"], first["supplier_phone"]), ("同名厂", "已有联系人", "123"))
        self.assertEqual((first["delivery_address"], first["recipient"], first["recipient_phone"]), ("", "", ""))
        self.assertEqual((item["item_name"], item["material"], item["dimension_text"], item["length"], item["width"], item["height"]), ("唛头 A", "AB", "40×30×20", 40, 30, 20))
        self.assertEqual((item["ordered_quantity"], item["unit_price_minor"], item["line_total_minor"], item["expected_at"], item["remark"]), (12, 325, 3900, "2020-02-08", "历史要求"))
        self.assertEqual([r["status"] for r in orders], ["ordered", "ordered", "draft", "ordered"])
        self.assertEqual([r["expected_at"] for r in items], ["2020-02-08", "2020-02-04", "2019-05-06", "2019-05-07"])
        meta = json.loads(orders[1]["legacy_metadata"])
        self.assertEqual((meta["completed"], meta["received_at"]), (1, ""))
        attachment = self.conn.execute("SELECT * FROM purchase_order_legacy_attachments").fetchone()
        self.assertEqual((attachment["purchase_order_id"], attachment["purchase_order_item_id"], attachment["legacy_source"], attachment["legacy_id"], attachment["stored_filename"], attachment["original_filename"]), (orders[2]["id"], items[2]["id"], "purchase_followups", 8, "kept.png", "原图.png"))
        for source, rows in old.items():
            self.assertEqual(rows, [tuple(r) for r in self.conn.execute(f"SELECT * FROM {source} ORDER BY id")])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='purchase_inventory_lots'").fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE legacy_source='arrival_records'").fetchone()[0], 0)

    def test_unknown_collision_is_blocking_without_hijack(self):
        for code, name in (("LEGACY-UNKNOWN", "真实厂"), ("REAL", "历史未指定供应商"), ("LEGACY-UNKNOWN", "历史未指定供应商")):
            with self.subTest(code=code, name=name):
                conn = sqlite3.connect(":memory:")
                try:
                    schema(conn)
                    insert(conn, "suppliers", code=code, name=name)
                    insert(conn, "purchase_followups", id=1, recorded_at="2020-01-01", item_name="未指定", quantity=2)
                    conn.commit()
                    self.assertTrue(callable(getattr(p, "migrate_legacy_procurement", None)))
                    p.migrate_legacy_procurement(conn, NOW)
                    p.migrate_legacy_procurement(conn, NOW)
                    self.assertEqual(tuple(conn.execute("SELECT code,name,active FROM suppliers").fetchone()), (code, name, 1))
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM supplier_legacy_links").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)
                    report = p.procurement_migration_report(conn)
                    self.assertEqual(report["blocking_unknown_conflicts"], 1)
                    self.assertEqual(report["sources"]["purchase_followups"]["conflicted"], 1)
                finally:
                    conn.close()

    def test_system_supplier_and_attachment_references_are_protected_without_fk_pragma(self):
        self.migrate()
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        for sql in (
            "UPDATE suppliers SET active=1 WHERE system_kind='legacy_unknown'",
            "UPDATE suppliers SET name='真实厂' WHERE system_kind='legacy_unknown'",
            "DELETE FROM suppliers WHERE system_kind='legacy_unknown'",
            "UPDATE purchase_order_legacy_attachments SET purchase_order_item_id=999",
            "UPDATE purchase_order_legacy_attachments SET purchase_order_id=1",
            "DELETE FROM purchase_order_items WHERE legacy_source='purchase_followups' AND legacy_id=8",
            "UPDATE purchase_order_items SET id=999 WHERE legacy_source='purchase_followups' AND legacy_id=8",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO purchase_order_legacy_attachments (purchase_order_id,purchase_order_item_id,legacy_source,legacy_id,stored_filename,original_filename) VALUES (999,999,'purchase_followups',999,'x.png','x.png')")

    def test_invalid_rows_are_conflicted_not_coerced_or_startup_fatal(self):
        cases = [("quantity", 0), ("quantity", -1), ("quantity", 1.5), ("quantity", 2147483648), ("unit_price", "NaN"), ("unit_price", float("inf")), ("unit_price", "1e999"), ("unit_price", -1), ("unit_price", 0.001), ("ordered_at", "2020-02-30"), ("received_at", "yesterday"), ("carton_length", -2)]
        for index, (field, value) in enumerate(cases, 100):
            fields = dict(id=index, ordered_at="2020-01-01", print_mark="坏历史", quantity=2, unit_price=1)
            fields[field] = value
            insert(self.conn, "carton_purchases", **fields)
        insert(self.conn, "purchase_followups", id=100, recorded_at="2020-01-01", purchased_at="2020-02-30", item_name="坏日期", quantity=1)
        self.conn.commit()
        self.assertEqual(self.migrate()["orders_created"], 4)
        report = self.report()
        self.assertEqual((report["sources"]["carton_purchases"]["old"], report["sources"]["carton_purchases"]["examined"], report["sources"]["carton_purchases"]["conflicted"]), (14, 14, 12))
        self.assertEqual(report["sources"]["purchase_followups"]["conflicted"], 1)
        before = self.conn.execute("SELECT COUNT(*) FROM procurement_migration_conflicts").fetchone()[0]
        self.migrate()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM procurement_migration_conflicts").fetchone()[0], before)

    def test_missing_followup_date_falls_back_deterministically(self):
        for identifier, recorded, created in ((20, "", "2021-03-04T12:34:56"), (21, "", "")):
            insert(self.conn, "purchase_followups", id=identifier, recorded_at=recorded, item_name="日期回退", quantity=1)
            self.conn.execute("UPDATE purchase_followups SET created_at=? WHERE id=?", (created, identifier))
        self.migrate()
        actual = [tuple(r) for r in self.conn.execute("SELECT purchased_at,status FROM purchase_orders WHERE legacy_source='purchase_followups' AND legacy_id>=20 ORDER BY legacy_id")]
        self.assertEqual(actual, [("2021-03-04", "draft"), ("2026-09-09", "draft")])

    def test_explicit_invalid_fallback_dates_and_timestamps_are_conflicts(self):
        for identifier, recorded, created, purchased in (
            (20, "bad", "2021-03-04T12:34:56", ""),
            (21, "", "bad", ""),
            (22, "", "2021-03-04garbage", ""),
            (23, "bad", NOW, "2020-01-01"),
            (24, "2020-01-01", "bad", "2020-01-01"),
        ):
            insert(self.conn, "purchase_followups", id=identifier, recorded_at=recorded, item_name="无效历史日期", quantity=1, purchased_at=purchased)
            self.conn.execute("UPDATE purchase_followups SET created_at=? WHERE id=?", (created, identifier))
        self.assertEqual(self.migrate()["orders_created"], 4)
        self.assertEqual(self.report()["sources"]["purchase_followups"]["conflicted"], 5)
        conflicts = self.conn.execute("SELECT incoming_value FROM procurement_migration_conflicts WHERE entity='order'").fetchall()
        self.assertEqual(len(conflicts), 5)
        self.assertTrue(all(json.loads(r[0])["item_name"] == "无效历史日期" for r in conflicts))

    def test_existing_unified_supplier_name_is_compared_after_trim(self):
        self.conn.execute("UPDATE suppliers SET name='  同名厂  '")
        self.migrate()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM suppliers WHERE trim(name)='同名厂'").fetchone()[0], 1)
        kept = self.conn.execute("SELECT code,name,contact,active FROM suppliers WHERE id=1").fetchone()
        self.assertEqual(tuple(kept), ("EXISTING", "  同名厂  ", "已有联系人", 0))

    def test_absent_sources_and_older_optional_columns_are_supported(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            p.ensure_procurement_tables(conn)
            self.assertTrue(callable(getattr(p, "migrate_legacy_procurement", None)))
            self.assertEqual(p.migrate_legacy_procurement(conn, NOW)["orders_created"], 0)
            self.assertEqual(p.procurement_migration_report(conn)["sources"]["carton_purchases"]["old"], 0)
            conn.execute("CREATE TABLE purchase_followups (id INTEGER PRIMARY KEY, recorded_at TEXT, item_name TEXT, quantity INTEGER, created_at TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO purchase_followups VALUES (1,'2020-01-02','早期记录',2,?,?)", (NOW, NOW))
            self.assertEqual(p.migrate_legacy_procurement(conn, NOW)["orders_created"], 1)
            self.assertEqual(tuple(conn.execute("SELECT status,purchased_at FROM purchase_orders").fetchone()), ("draft", "2020-01-02"))
            # A malformed source schema is not mistaken for an empty source.
            conn.execute("CREATE TABLE carton_purchases (wrong_id INTEGER)")
            with self.assertRaises(sqlite3.OperationalError):
                p.migrate_legacy_procurement(conn, NOW)

    def test_unexpected_error_rolls_back_entire_migration_and_caller_can_rollback(self):
        self.conn.execute("CREATE TRIGGER fail_migration BEFORE INSERT ON purchase_order_items WHEN NEW.legacy_id=8 BEGIN SELECT RAISE(ABORT, 'unexpected fixture failure'); END")
        self.conn.commit()
        self.assertTrue(callable(getattr(p, "migrate_legacy_procurement", None)))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "unexpected fixture failure"):
            self.migrate()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM supplier_legacy_links").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0], 1)
        self.conn.execute("DROP TRIGGER fail_migration")
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.migrate()
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)

    def test_report_reconciliation_coverage_and_strictly_no_writes(self):
        self.migrate()
        self.conn.commit()
        with sqlite3.connect(":memory:") as copy:
            self.conn.backup(copy)
            copy.row_factory = sqlite3.Row
            report = p.procurement_migration_report(copy)
            self.assertEqual(copy.total_changes, 0)
        sources = report["sources"]
        for source, want in (("carton_purchases", (2, 14, 3920)), ("purchase_followups", (2, 7, 0))):
            row = sources[source]
            self.assertEqual((row["old"], row["examined"], row["migrated"], row["conflicted"], row["deferred"]), (want[0], want[0], want[0], 0, 0))
            self.assertEqual((row["old_quantity"], row["new_quantity"], row["old_amount_minor"], row["new_amount_minor"]), (want[1], want[1], want[2], want[2]))
        arrival = sources["arrival_records"]
        self.assertEqual((arrival["old"], arrival["examined"], arrival["migrated"], arrival["deferred"], arrival["old_quantity"], arrival["old_amount_minor"], arrival["new_quantity"]), (1, 1, 0, 1, 5, 1005, 0))
        self.assertEqual(report["derived_expected_dates"], 3)
        self.assertEqual(report["attachments"], {"old": 1, "linked": 1, "unlinked": 0})
        self.assertEqual(report["unknown_links"], 4)
        self.assertEqual(report["duplicate_source_keys"], [])
        self.assertEqual(report["foreign_key_check"], [])
        for source in SUPPLIER_SOURCES:
            self.assertEqual(report["supplier_sources"][source]["unlinked"], 0)

    def test_report_exposes_duplicate_and_foreign_key_faults(self):
        self.migrate()
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute("DROP INDEX idx_purchase_order_items_legacy_source_id")
        self.conn.execute("UPDATE purchase_order_items SET legacy_id=7 WHERE legacy_source='carton_purchases' AND legacy_id=8")
        self.conn.execute("DROP TRIGGER trg_purchase_order_items_require_order_update")
        self.conn.execute("DROP TRIGGER trg_legacy_attachment_restrict_item_identity")
        self.conn.execute("UPDATE purchase_order_items SET purchase_order_id=999 WHERE legacy_source='purchase_followups'")
        report = self.report()
        self.assertEqual(report["duplicate_source_keys"][0], {"table": "purchase_order_items", "legacy_source": "carton_purchases", "legacy_id": 7, "count": 2})
        self.assertEqual(len(report["foreign_key_check"]), 2)

    def test_cli_sorted_json_readonly_bytes_mtime_and_missing_schema_error(self):
        self.migrate()
        self.conn.commit()
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "fixture # report.db"
            image = Path(directory) / "kept.png"
            image.write_bytes(b"historical attachment: do not replace")
            image_before = (image.read_bytes(), image.stat().st_mtime_ns)
            with sqlite3.connect(db) as copy:
                self.conn.backup(copy)
            before = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns)
            cmd = [sys.executable, str(ROOT / "scripts/report_procurement_migration.py"), "--database", str(db)]
            first = subprocess.run(cmd, capture_output=True, text=True)
            second = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(first.stdout, json.dumps(json.loads(first.stdout), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            self.assertEqual(before, (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns))
            self.assertEqual(image_before, (image.read_bytes(), image.stat().st_mtime_ns))
            absent = Path(directory) / "missing.db"
            failed = subprocess.run(cmd[:-1] + [str(absent)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(absent.exists())
            empty = Path(directory) / "empty.db"
            sqlite3.connect(empty).close()
            failed = subprocess.run(cmd[:-1] + [str(empty)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("schema", failed.stderr.lower())
            self.assertEqual(empty.read_bytes(), b"")


class MigrationRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        paths = {name: root / name.lower() for name in ("DATA_DIR", "MANUALS_DIR", "SIGNATURES_DIR", "RECONCILIATION_SIGNATURES_DIR", "SHIPMENT_IMAGES_DIR", "INSPECTION_REPORTS_DIR", "PRODUCTION_DRAWINGS_DIR")}
        paths.update(DB_PATH=root / "fixture.db", DATABASE_READY=False)
        self.patch = patch.multiple(app, **paths)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.config = patch.dict(app.app.config, TESTING=True, SECRET_KEY="migration-test", WRITE_LOCK_PATH=str(root / "write.lock"))
        self.config.start()
        self.addCleanup(self.config.stop)
        with sqlite3.connect(app.DB_PATH) as conn:
            schema(conn)
            fixture(conn)
        app.init_db()
        app.DATABASE_READY = True
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session.update(admin_logged_in=True, admin_username="admin")

    def test_startup_migrates_after_sources_exist_and_is_idempotent(self):
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 4)
            insert(conn, "carton_purchases", id=99, ordered_at="bad", quantity=0)
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM procurement_migration_conflicts WHERE entity='order' AND legacy_id=99").fetchone()[0], 1)

    def test_startup_unexpected_error_rolls_back(self):
        with app.get_db() as conn:
            insert(conn, "purchase_followups", id=99, item_name="触发错误", recorded_at="2020-01-01", quantity=1)
            conn.execute("CREATE TRIGGER startup_failure BEFORE INSERT ON purchase_order_items WHEN NEW.legacy_id=99 BEGIN SELECT RAISE(ABORT, 'startup programming error'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "startup programming error"):
            app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE legacy_id=99").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM supplier_legacy_links WHERE legacy_source='purchase_followups' AND legacy_id=99").fetchone()[0], 0)

    def test_bookmarks_redirect_and_all_legacy_writes_return_409_without_mutation(self):
        for old, new in (("purchase-followups", "other"), ("carton-purchases", "carton")):
            response = self.client.get(f"/admin/{old}?q=historical")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, f"/admin/purchases/{new}")
        with app.get_db() as conn:
            before = list(conn.iterdump())
        paths = ["/admin/purchase-followups", "/admin/carton-purchases", "/admin/orders/carton-purchases", "/admin/carton-products", "/admin/carton-suppliers"]
        paths += [f"/admin/purchase-followups/8/{action}" for action in ("edit", "copy", "toggle", "image/delete", "delete")]
        paths += [f"/admin/carton-purchases/7/{action}" for action in ("edit", "copy", "complete", "delete")]
        paths += [f"/admin/carton-products/3/{action}" for action in ("edit", "copy", "delete")]
        paths += [f"/admin/carton-suppliers/1/{action}" for action in ("edit", "delete")]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.post(path, data=dict(item_name="不能写入", quantity="2", recorded_at="2020-01-01"))
                self.assertEqual(response.status_code, 409)
        with app.get_db() as conn:
            self.assertEqual(list(conn.iterdump()), before)
        self.assertEqual(self.client.get("/admin/carton-purchases/statement").status_code, 200)
        response = self.client.get("/admin/carton-purchases/7/purchase-order")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_arrival_remains_writable_and_navigation_uses_unified_entries(self):
        response = self.client.post("/admin/arrival-records", data=dict(arrived_at="2020-05-06", item_name="继续使用", quantity="2", unit_price="1", supplier_name="供方"))
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM arrival_records").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE legacy_source='arrival_records'").fetchone()[0], 0)
        for path in ("/admin/purchases/raw-material", "/admin/purchases/carton", "/admin/purchases/outsourcing", "/admin/purchases/other", "/admin/purchases/orders/1", "/admin/purchases/orders/1/edit", "/admin/arrival-records"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                top = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
                self.assertRegex(top, r'class="[^"]*active-nav-link[^"]*" href="/admin/purchases/raw-material">采购</a>')
                self.assertEqual(top.count(">客商管理</a>"), 1)
                self.assertNotIn(">其他采购</a>", top)
                self.assertNotIn(">纸箱采购</a>", top)
                self.assertIn('aria-label="采购分类"', html)
                self.assertIn("到货记录（历史）", html)
        for path in ("/dashboard", "/admin"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn("统一采购", html)
            self.assertNotIn('href="/admin/purchase-followups"', html)
            self.assertNotIn('href="/admin/carton-purchases"', html)

    def test_navigation_arrival_tab_obeys_legacy_permission(self):
        with app.get_db() as conn:
            insert(conn, "users", username="view-only", password_hash="hash", role="operator", can_view_purchases=1, can_manage_purchases=0, can_manage_carton_purchases=0, can_manage_purchase_followups=0, can_manage_suppliers=0, can_manage_customers=0, can_manage_common_info=0)
        with self.client.session_transaction() as session:
            session["admin_username"] = "view-only"
        html = self.client.get("/admin/purchases/other").get_data(as_text=True)
        self.assertIn(">采购</a>", html)
        self.assertNotIn("到货记录（历史）", html)
        self.assertNotIn(">客商管理</a>", html)
        self.assertEqual(self.client.get("/admin/arrival-records").status_code, 302)
        with app.get_db() as conn:
            conn.execute("UPDATE users SET can_view_purchases=0,can_manage_carton_purchases=1 WHERE username='view-only'")
        html = self.client.get("/admin/arrival-records").get_data(as_text=True)
        self.assertIn('href="/admin/arrival-records">采购</a>', html)
        self.assertNotIn('href="/admin/purchases/raw-material"', html)
        self.assertIn("到货记录（历史）", html)

    def test_unknown_supplier_is_readonly_in_management_and_excluded_from_new_orders(self):
        with app.get_db() as conn:
            identifier = conn.execute("SELECT id FROM suppliers WHERE code='LEGACY-UNKNOWN'").fetchone()[0]
        for action in ("edit", "delete", "reactivate"):
            response = self.client.post(f"/admin/business-partners/suppliers/{identifier}/{action}", data=dict(code="REAL", name="不能劫持"))
            self.assertEqual(response.status_code, 409)
        html = self.client.get("/admin/business-partners/suppliers").get_data(as_text=True)
        self.assertIn("历史系统档案（只读）", html)
        self.assertNotIn(f'/suppliers/{identifier}/reactivate', html)
        html = self.client.get("/admin/purchases/other/new").get_data(as_text=True)
        self.assertNotIn(f'<option value="{identifier}">历史未指定供应商', html)


if __name__ == "__main__":
    unittest.main()
