import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app


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


if __name__ == "__main__":
    unittest.main()
