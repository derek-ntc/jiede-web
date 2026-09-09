import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app
import production_processes


class ProductionFollowupCustomerTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.original_drawings_dir = app.PRODUCTION_DRAWINGS_DIR
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        app.DB_PATH = root / "manuals.db"
        app.PRODUCTION_DRAWINGS_DIR = root / "production-drawings"
        app.DATABASE_READY = False
        app.init_db()

        now = "2026-09-08T09:00:00"
        with app.get_db() as conn:
            self.product_a = self._insert_product(
                conn, "P1", "客户A专用产品", "客户A", "规格-A", now
            )
            self.product_b = self._insert_product(
                conn, "P1", "客户B专用产品", "客户B", "规格-B", now
            )
            self.product_a_other = self._insert_product(
                conn, "P2", "客户A另一产品", "客户A", "规格-A2", now
            )
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_manage_production_followups, created_at, updated_at
                ) VALUES ('manager', 'unused', 'operator', 1, 1, ?, ?)
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_manage_production_followups, created_at, updated_at
                ) VALUES ('viewer', 'unused', 'operator', 1, 0, ?, ?)
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO production_followups (
                    id, batch_no, customer, manual_id, ordered_at, drawing_no,
                    product_name, created_at, updated_at
                ) VALUES
                    (1, 'PF-20260908-001', '客户A', ?, '2026-09-08', 'P1',
                     '客户A专用产品', ?, ?),
                    (2, 'PF-20260908-002', '客户B', ?, '2026-09-08', 'P1',
                     '客户B专用产品', ?, ?),
                    (3, 'PF-20260908-003', '客户A', ?, '2026-09-08', 'P2',
                     '客户A另一产品', ?, ?),
                    (4, 'PF-20260908-004', '', NULL, '2026-09-08', 'OLD',
                     '旧未归类产品', ?, ?)
                """,
                (
                    self.product_a,
                    now,
                    now,
                    self.product_b,
                    now,
                    now,
                    self.product_a_other,
                    now,
                    now,
                    now,
                    now,
                ),
            )

        self.client = app.app.test_client()
        self._login("manager")

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        app.PRODUCTION_DRAWINGS_DIR = self.original_drawings_dir
        self.tmpdir.cleanup()

    def _login(self, username):
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["admin_role"] = "operator"

    @staticmethod
    def _insert_product(conn, drawing_no, product_name, customer, specification, now):
        return conn.execute(
            """
            INSERT INTO manuals (
                drawing_no, product_name, customer, supplier, model, category,
                version, remark, filename, original_filename, created_at, updated_at,
                unit_price_minor, currency
            ) VALUES (?, ?, ?, ?, 'legacy-model', '', '', '', '', '', ?, ?, 12345, 'CNY')
            """,
            (drawing_no, product_name, customer, specification, now, now),
        ).lastrowid

    def _csrf_token(self):
        html = self.client.get("/admin/production-followups").get_data(as_text=True)
        match = re.search(
            r'name="production_followup_csrf_token" value="([^"]+)"', html
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def test_schema_migration_is_idempotent_and_keeps_legacy_records_unassigned(self):
        app.init_db()
        app.init_db()

        with app.get_db() as conn:
            columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(production_followups)")
            }
            legacy = conn.execute(
                "SELECT customer, manual_id FROM production_followups WHERE id = 4"
            ).fetchone()

        self.assertEqual(columns["customer"]["notnull"], 1)
        self.assertEqual(columns["customer"]["dflt_value"], "''")
        self.assertEqual(columns["manual_id"]["notnull"], 0)
        self.assertEqual(tuple(legacy), ("", None))

    def test_customer_and_keyword_filters_are_combined_and_unassigned_is_separate(self):
        with app.get_db() as conn:
            customer_and_query = app.fetch_production_followups(
                conn, "P1", "客户A"
            )
            unassigned = app.fetch_production_followups(
                conn, customer="__unassigned__"
            )

        self.assertEqual(
            [item["row"]["id"] for item in customer_and_query], [1]
        )
        self.assertEqual([item["row"]["id"] for item in unassigned], [4])

        response = self.client.get(
            "/admin/production-followups",
            query_string={"customer": "客户A", "q": "P1"},
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("客户A专用产品", html)
        self.assertNotIn("客户B专用产品", html)

        product_name_response = self.client.get(
            "/admin/production-followups",
            query_string={"customer": "客户A", "q": "另一产品"},
        )
        product_name_html = product_name_response.get_data(as_text=True)
        self.assertIn("客户A另一产品", product_name_html)
        self.assertNotIn("客户A专用产品", product_name_html)

    def test_production_list_renders_snapshot_processes_and_filter_preserving_actions(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_a,
                ["下料", "钻孔", "包装"],
                now="2026-09-09T09:00:00",
            )
            steps = production_processes.create_followup_process_snapshot(
                conn,
                1,
                self.product_a,
                now="2026-09-09T09:05:00",
            )

        response = self.client.get(
            "/admin/production-followups",
            query_string={"customer": "客户A", "q": "P1"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("下料", html)
        self.assertIn("钻孔", html)
        self.assertIn("包装", html)
        self.assertIn(
            f'/admin/production-followups/1/processes/{steps[0]["id"]}/complete',
            html,
        )
        self.assertNotIn(
            f'/admin/production-followups/1/processes/{steps[1]["id"]}/complete',
            html,
        )
        self.assertIn(
            f'/admin/production-followups/1/processes/{steps[1]["id"]}/move',
            html,
        )
        self.assertIn('/admin/production-followups/1/processes', html)
        self.assertIn('name="filter_q" value="P1"', html)
        self.assertIn('name="filter_customer" value="客户A"', html)
        self.assertNotIn('/admin/production-followups/1/laser', html)
        self.assertNotIn("待激光", html)
        self.assertNotIn("待折弯", html)

    def test_production_list_backfills_and_renders_a_legacy_followup_card(self):
        app.DATABASE_READY = True
        response = self.client.get(
            "/admin/production-followups",
            query_string={"customer": "__unassigned__", "q": "OLD"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("<strong>激光</strong>"), html.index("<strong>折弯</strong>"))
        self.assertLess(html.index("<strong>折弯</strong>"), html.index("<strong>焊接</strong>"))
        with app.get_db() as conn:
            marker = conn.execute(
                "SELECT process_snapshot_created FROM production_followups WHERE id = 4"
            ).fetchone()["process_snapshot_created"]
            steps = production_processes.load_followup_process_card(conn, 4)
        self.assertEqual(marker, 1)
        self.assertEqual([step["name"] for step in steps], ["激光", "折弯", "焊接"])

    def test_process_routes_delegate_add_move_complete_revert_and_delete_rules(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_a,
                ["下料", "检验"],
                now="2026-09-09T09:00:00",
            )
            initial = production_processes.create_followup_process_snapshot(
                conn,
                1,
                self.product_a,
                now="2026-09-09T09:05:00",
            )

        add_response = self.client.post(
            "/admin/production-followups/1/processes",
            data={
                "name": "  包装  ",
                "filter_q": "P1",
                "filter_customer": "客户A",
            },
        )
        location = urlsplit(add_response.headers["Location"])
        self.assertEqual(location.path, "/admin/production-followups")
        self.assertEqual(
            parse_qs(location.query),
            {"q": ["P1"], "customer": ["客户A"]},
        )
        with app.get_db() as conn:
            after_add = production_processes.load_followup_process_card(conn, 1)
        added = after_add[-1]
        self.assertEqual([step["name"] for step in after_add], ["下料", "检验", "包装"])

        self.client.post(
            f'/admin/production-followups/1/processes/{added["id"]}/move',
            data={"direction": "up"},
        )
        denied_completion = self.client.post(
            f'/admin/production-followups/1/processes/{initial[1]["id"]}/complete',
            follow_redirects=True,
        )
        self.assertIn("只能完成第一项未完成工艺", denied_completion.get_data(as_text=True))

        self.client.post(
            f'/admin/production-followups/1/processes/{initial[0]["id"]}/complete'
        )
        denied_delete = self.client.post(
            f'/admin/production-followups/1/processes/{initial[0]["id"]}/delete',
            follow_redirects=True,
        )
        self.assertIn("已完成工艺必须先撤回再删除", denied_delete.get_data(as_text=True))

        self.client.post(
            f'/admin/production-followups/1/processes/{initial[0]["id"]}/revert'
        )
        self.client.post(
            f'/admin/production-followups/1/processes/{initial[1]["id"]}/delete'
        )

        with app.get_db() as conn:
            final = production_processes.load_followup_process_card(conn, 1)
        self.assertEqual(
            [(step["name"], step["completed_at"], step["completed_by"]) for step in final],
            [("下料", "", ""), ("包装", "", "")],
        )

    def test_process_routes_return_not_found_for_an_unknown_followup(self):
        response = self.client.post(
            "/admin/production-followups/999999/processes",
            data={"name": "下料"},
        )

        self.assertEqual(response.status_code, 404)

    def test_printable_process_card_renders_two_copies_of_every_snapshot_step(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_a,
                ["下料", "钻孔", "检验", "包装"],
                now="2026-09-09T09:00:00",
            )
            steps = production_processes.create_followup_process_snapshot(
                conn,
                1,
                self.product_a,
                now="2026-09-09T09:05:00",
            )
            production_processes.complete_followup_process_step(
                conn,
                1,
                steps[0]["id"],
                "operator-a",
                completed_at="2026-09-09T10:10:00+08:00",
            )
            production_processes.complete_followup_process_step(
                conn,
                1,
                steps[1]["id"],
                "operator-b",
                completed_at="2026-09-09T10:20:00+08:00",
            )

        response = self.client.get("/admin/production-followups/1/process-card")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('<article class="process-card">'), 2)
        for process_name in ["下料", "钻孔", "检验", "包装"]:
            self.assertEqual(html.count(f"<td>{process_name}</td>"), 2)
        self.assertEqual(html.count("operator-a"), 2)
        self.assertEqual(html.count("operator-b"), 2)
        self.assertEqual(html.count("2026-09-09 10:10"), 2)
        self.assertEqual(html.count("2026-09-09 10:20"), 2)

    def test_product_lookup_is_customer_scoped_permission_guarded_and_price_safe(self):
        response = self.client.get(
            "/admin/production-followups/products",
            query_string={"customer": "客户A", "q": "P1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "products": [
                    {
                        "id": self.product_a,
                        "drawing_no": "P1",
                        "product_name": "客户A专用产品",
                        "specification": "规格-A",
                    }
                ]
            },
        )
        self.assertNotIn("price", response.get_data(as_text=True).lower())
        self.assertNotIn("unit_price_minor", response.get_data(as_text=True))

        self._login("viewer")
        denied = self.client.get(
            "/admin/production-followups/products",
            query_string={"customer": "客户A", "q": "P1"},
        )
        self.assertEqual(denied.status_code, 302)

    def test_selected_product_must_match_customer_and_text_but_manual_entry_stays_available(self):
        bad_posts = [
            {
                "customer": "客户B",
                "manual_id": str(self.product_a),
                "drawing_no": "P1",
                "product_name": "客户A专用产品",
            },
            {
                "customer": "客户A",
                "manual_id": str(self.product_a),
                "drawing_no": "TAMPERED",
                "product_name": "客户A专用产品",
            },
            {
                "customer": "__unassigned__",
                "manual_id": "",
                "drawing_no": "MANUAL-BAD",
                "product_name": "不能存哨兵",
            },
        ]
        for fields in bad_posts:
            with self.subTest(fields=fields):
                response = self.client.post(
                    "/admin/production-followups",
                    data={"ordered_at": "2026-09-09", **fields},
                )
                self.assertEqual(response.status_code, 302)

        manual_response = self.client.post(
            "/admin/production-followups",
            data={
                "ordered_at": "2026-09-09",
                "customer": "客户A",
                "manual_id": "",
                "drawing_no": "MANUAL-1",
                "product_name": "手填产品",
            },
        )
        self.assertEqual(manual_response.status_code, 302)
        selected_response = self.client.post(
            "/admin/production-followups",
            data={
                "ordered_at": "2026-09-09",
                "customer": "客户A",
                "manual_id": str(self.product_a),
                "drawing_no": "P1",
                "product_name": "客户A专用产品",
            },
        )
        self.assertEqual(selected_response.status_code, 302)

        with app.get_db() as conn:
            added = conn.execute(
                """
                SELECT customer, manual_id, drawing_no, product_name
                FROM production_followups
                WHERE ordered_at = '2026-09-09'
                ORDER BY id
                """
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in added],
            [
                ("客户A", None, "MANUAL-1", "手填产品"),
                ("客户A", self.product_a, "P1", "客户A专用产品"),
            ],
        )

    def test_customer_classification_requires_permission_and_csrf_without_losing_stage_or_file(self):
        now = "2026-09-08T10:00:00"
        with app.get_db() as conn:
            conn.execute(
                "UPDATE production_followups SET laser_completed_at = ? WHERE id = 4",
                (now,),
            )
            conn.execute(
                """
                INSERT INTO production_followup_files (
                    followup_id, filename, original_filename, created_at
                ) VALUES (4, 'legacy.pdf', '旧图纸.pdf', ?)
                """,
                (now,),
            )

        token = self._csrf_token()
        bad_csrf = self.client.post(
            "/admin/production-followups/4/customer",
            data={"customer": "客户A", "production_followup_csrf_token": "bad"},
        )
        self.assertEqual(bad_csrf.status_code, 403)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT customer FROM production_followups WHERE id = 4"
                ).fetchone()["customer"],
                "",
            )

        self._login("viewer")
        denied = self.client.post(
            "/admin/production-followups/4/customer",
            data={"customer": "客户A", "production_followup_csrf_token": token},
        )
        self.assertEqual(denied.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT customer FROM production_followups WHERE id = 4"
                ).fetchone()["customer"],
                "",
            )

        self._login("manager")
        updated = self.client.post(
            "/admin/production-followups/4/customer",
            data={"customer": "客户A", "production_followup_csrf_token": token},
        )
        self.assertEqual(updated.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                "SELECT customer, manual_id, laser_completed_at FROM production_followups WHERE id = 4"
            ).fetchone()
            files = conn.execute(
                "SELECT original_filename FROM production_followup_files WHERE followup_id = 4"
            ).fetchall()
        self.assertEqual(tuple(followup), ("客户A", None, now))
        self.assertEqual([row["original_filename"] for row in files], ["旧图纸.pdf"])

    def test_customer_classification_rejects_non_ascii_csrf_without_side_effects(self):
        self._csrf_token()
        response = self.client.post(
            "/admin/production-followups/4/customer",
            data={
                "customer": "客户A",
                "production_followup_csrf_token": "中文令牌",
            },
        )
        self.assertEqual(response.status_code, 403)
        with app.get_db() as conn:
            followup = conn.execute(
                "SELECT customer, manual_id FROM production_followups WHERE id=4"
            ).fetchone()
        self.assertEqual(tuple(followup), ("", None))

    def test_stage_redirect_and_rendered_action_forms_preserve_active_filters(self):
        response = self.client.post(
            "/admin/production-followups/1/laser",
            query_string={"q": "P1", "customer": "客户A"},
        )
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.path, "/admin/production-followups")
        self.assertEqual(parse_qs(location.query), {"q": ["P1"], "customer": ["客户A"]})

        html = self.client.get(
            "/admin/production-followups",
            query_string={"q": "P1", "customer": "客户A"},
        ).get_data(as_text=True)
        self.assertIn('name="filter_q" value="P1"', html)
        self.assertIn('name="filter_customer" value="客户A"', html)
        self.assertIn("/static/production-followups.js", html)

    def test_new_followup_copies_the_selected_products_process_template(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_a,
                ["下料", "打磨", "包装"],
                now="2026-09-09T09:00:00",
            )

        response = self.client.post(
            "/admin/production-followups",
            data={
                "ordered_at": "2026-09-10",
                "customer": "客户A",
                "manual_id": str(self.product_a),
                "drawing_no": "P1",
                "product_name": "客户A专用产品",
            },
        )
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                """
                SELECT id, process_snapshot_created
                FROM production_followups
                WHERE ordered_at = '2026-09-10'
                """
            ).fetchone()
            steps = production_processes.load_followup_process_card(
                conn, followup["id"]
            )

        self.assertEqual(followup["process_snapshot_created"], 1)
        self.assertEqual(
            [(step["name"], step["sort_order"]) for step in steps],
            [("下料", 0), ("打磨", 1), ("包装", 2)],
        )

    def test_legacy_stage_route_updates_the_matching_process_snapshot_step(self):
        response = self.client.post("/admin/production-followups/1/laser")
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                "SELECT laser_completed_at FROM production_followups WHERE id = 1"
            ).fetchone()
            card = production_processes.load_followup_process_card(conn, 1)

        self.assertEqual([step["name"] for step in card], ["激光", "折弯", "焊接"])
        self.assertEqual(card[0]["completed_at"], followup["laser_completed_at"])
        self.assertEqual(card[0]["completed_by"], "manager")

        reverted_response = self.client.post("/admin/production-followups/1/laser")
        self.assertEqual(reverted_response.status_code, 302)
        with app.get_db() as conn:
            reverted_followup = conn.execute(
                "SELECT laser_completed_at FROM production_followups WHERE id = 1"
            ).fetchone()
            reverted_card = production_processes.load_followup_process_card(conn, 1)
        self.assertEqual(reverted_followup["laser_completed_at"], "")
        self.assertEqual(reverted_card[0]["completed_at"], "")
        self.assertEqual(reverted_card[0]["completed_by"], "")

    def test_legacy_stage_route_rejects_a_stage_missing_from_explicit_empty_card(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_a,
                [],
                now="2026-09-09T09:00:00",
            )
            before_card = production_processes.create_followup_process_snapshot(
                conn,
                1,
                self.product_a,
                now="2026-09-09T09:05:00",
            )

        response = self.client.post("/admin/production-followups/1/laser")
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                """
                SELECT laser_completed_at, bending_completed_at,
                       welding_completed_at
                FROM production_followups WHERE id = 1
                """
            ).fetchone()
            card = production_processes.load_followup_process_card(conn, 1)

        self.assertEqual(card, before_card)
        self.assertEqual(tuple(followup), ("", "", ""))

    def test_legacy_stage_route_rejects_a_stage_missing_from_custom_card(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.product_b,
                ["下料", "包装"],
                now="2026-09-09T09:00:00",
            )
            before_card = production_processes.create_followup_process_snapshot(
                conn,
                2,
                self.product_b,
                now="2026-09-09T09:05:00",
            )

        response = self.client.post("/admin/production-followups/2/laser")
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                """
                SELECT laser_completed_at, bending_completed_at,
                       welding_completed_at
                FROM production_followups WHERE id = 2
                """
            ).fetchone()
            card = production_processes.load_followup_process_card(conn, 2)

        self.assertEqual(card, before_card)
        self.assertEqual(tuple(followup), ("", "", ""))

    def test_deleting_followup_removes_its_process_snapshot_rows(self):
        response = self.client.post("/admin/production-followups/2/delete")
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            followup = conn.execute(
                "SELECT id FROM production_followups WHERE id = 2"
            ).fetchone()
            process_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM production_followup_process_steps
                WHERE followup_id = 2
                """
            ).fetchone()["c"]

        self.assertIsNone(followup)
        self.assertEqual(process_count, 0)

    def test_product_picker_debounces_aborts_and_clears_customer_association(self):
        script = r'''
        (async () => {
          const assert = require('node:assert/strict');
          const fs = require('node:fs');
          const vm = require('node:vm');

          class Element {
            constructor(value = '') {
              this.value = value;
              this.dataset = {};
              this.children = [];
              this.listeners = {};
              this.hidden = false;
              this.selectedIndex = 0;
            }
            addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
            async emit(type) { for (const fn of this.listeners[type] || []) await fn({target: this}); }
            append(...items) { this.children.push(...items); }
            replaceChildren(...items) { this.children = items; }
          }

          const customer = new Element('客户A');
          const search = new Element('P1');
          const results = new Element();
          const manualId = new Element();
          const drawingNo = new Element();
          const productName = new Element();
          const nodes = {customer, search, results, manualId, drawingNo, productName};
          const form = new Element();
          form.dataset.productsUrl = '/admin/production-followups/products';
          form.querySelector = selector => nodes[{
            '[data-production-customer]': 'customer',
            '[data-production-product-search]': 'search',
            '[data-production-product-results]': 'results',
            '[data-production-manual-id]': 'manualId',
            '[data-production-drawing-no]': 'drawingNo',
            '[data-production-product-name]': 'productName',
          }[selector]] || null;

          let nextTimer = 1;
          const timers = new Map();
          const fireTimer = () => {
            assert.equal(timers.size, 1, 'exactly one debounced request is pending');
            const [id, callback] = timers.entries().next().value;
            timers.delete(id);
            return callback();
          };
          global.window = {
            location: {origin: 'https://test.local'},
            setTimeout(callback) { const id = nextTimer++; timers.set(id, callback); return id; },
            clearTimeout(id) { timers.delete(id); },
          };
          global.document = {
            querySelector(selector) {
              if (selector === '.production-followup-form') return form;
              return null;
            },
            querySelectorAll() { return []; },
            createElement() { return new Element(); },
          };

          const requests = [];
          global.fetch = (url, options) => new Promise((resolve, reject) => {
            const request = {url: String(url), options, resolve, reject};
            requests.push(request);
            options.signal.addEventListener('abort', () => {
              const error = new Error('aborted');
              error.name = 'AbortError';
              reject(error);
            }, {once: true});
          });
          const response = products => ({ok: true, status: 200, json: async () => ({products})});

          vm.runInThisContext(fs.readFileSync('static/production-followups.js', 'utf8'));

          await search.emit('input');
          search.value = 'P1 latest';
          await search.emit('input');
          assert.equal(timers.size, 1, 'rapid input replaces the older debounce timer');
          const firstLoad = fireTimer();
          assert.match(requests[0].url, /customer=%E5%AE%A2%E6%88%B7A/);
          assert.match(requests[0].url, /q=P1\+latest/);
          requests[0].resolve(response([{id: 1, drawing_no: 'P1', product_name: '客户A产品', specification: '规格A'}]));
          await firstLoad;
          results.value = '1';
          await results.emit('change');
          assert.equal(manualId.value, '1');
          assert.equal(drawingNo.value, 'P1');
          assert.equal(productName.value, '客户A产品');

          search.value = 'slow';
          await search.emit('input');
          const slowLoad = fireTimer();
          const slowRequest = requests[1];
          assert.equal(slowRequest.options.signal.aborted, false);

          customer.value = '客户B';
          await customer.emit('change');
          assert.equal(manualId.value, '', 'customer changes immediately clear the product association');
          assert.equal(results.selectedIndex, -1);
          assert.equal(slowRequest.options.signal.aborted, true, 'customer changes abort the old request');
          await slowLoad;

          const customerBLoad = fireTimer();
          assert.match(requests[2].url, /customer=%E5%AE%A2%E6%88%B7B/);
          requests[2].resolve(response([{id: 2, drawing_no: 'P2', product_name: '客户B产品', specification: '规格B'}]));
          await customerBLoad;
          assert.deepEqual(results.children.slice(1).map(option => option.value), ['2']);
          assert.equal(manualId.value, '');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        '''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=Path(app.__file__).parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_customer_rename_keeps_linked_and_manual_followups_under_new_classification(self):
        with app.get_db() as conn:
            customer_id = conn.execute(
                "INSERT INTO customers (name, created_at, updated_at) VALUES ('客户A', '', '')"
            ).lastrowid
            conn.execute(
                """
                INSERT INTO production_followups (
                    id, batch_no, customer, manual_id, ordered_at, drawing_no,
                    product_name, laser_completed_at, created_at, updated_at
                ) VALUES (
                    5, 'PF-20260908-005', '客户A', NULL, '2026-09-08', 'MANUAL',
                    '手工录入产品', '2026-09-08T10:00:00',
                    '2026-09-08T09:00:00', '2026-09-08T09:00:00'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO production_followup_files (
                    followup_id, filename, original_filename, created_at
                ) VALUES (5, 'manual.pdf', '手工图纸.pdf', '2026-09-08T09:00:00')
                """
            )

        response = self.client.post(
            f"/admin/customers/{customer_id}/edit",
            data={"name": "客户A（改名）"},
        )
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            renamed = app.fetch_production_followups(
                conn, customer="客户A（改名）"
            )
            old_name = app.fetch_production_followups(conn, customer="客户A")
            rows = conn.execute(
                """
                SELECT id, customer, manual_id, laser_completed_at, updated_at
                FROM production_followups
                WHERE id IN (1, 4, 5)
                ORDER BY id
                """
            ).fetchall()
            files = conn.execute(
                "SELECT original_filename FROM production_followup_files WHERE followup_id = 5"
            ).fetchall()

        self.assertEqual([item["row"]["id"] for item in renamed], [5, 3, 1])
        self.assertEqual([item["row"]["id"] for item in old_name], [])
        self.assertEqual(
            [tuple(row[:4]) for row in rows],
            [
                (1, "客户A（改名）", self.product_a, ""),
                (4, "", None, ""),
                (5, "客户A（改名）", None, "2026-09-08T10:00:00"),
            ],
        )
        self.assertNotEqual(rows[0]["updated_at"], "2026-09-08T09:00:00")
        self.assertEqual(rows[1]["updated_at"], "2026-09-08T09:00:00")
        self.assertNotEqual(rows[2]["updated_at"], "2026-09-08T09:00:00")
        self.assertEqual([row["original_filename"] for row in files], ["手工图纸.pdf"])

    def test_customer_rename_rolls_back_every_cascade_when_followup_update_fails(self):
        with app.get_db() as conn:
            customer_id = conn.execute(
                "INSERT INTO customers (name, created_at, updated_at) VALUES ('客户A', '', '')"
            ).lastrowid
            conn.execute(
                """
                CREATE TRIGGER reject_production_customer_rename
                BEFORE UPDATE OF customer ON production_followups
                WHEN OLD.id = 1
                BEGIN
                    SELECT RAISE(ABORT, '故障注入');
                END
                """
            )

        response = self.client.post(
            f"/admin/customers/{customer_id}/edit",
            data={"name": "不应保存的新名"},
        )
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            customer_name = conn.execute(
                "SELECT name FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()["name"]
            manual_customer = conn.execute(
                "SELECT customer FROM manuals WHERE id = ?", (self.product_a,)
            ).fetchone()["customer"]
            followup = conn.execute(
                "SELECT customer, updated_at FROM production_followups WHERE id = 1"
            ).fetchone()

        self.assertEqual(customer_name, "客户A")
        self.assertEqual(manual_customer, "客户A")
        self.assertEqual(tuple(followup), ("客户A", "2026-09-08T09:00:00"))


if __name__ == "__main__":
    unittest.main()
