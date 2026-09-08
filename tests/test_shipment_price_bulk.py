"""Bulk historical pricing exercises real SQLite sources and signed HTTP previews."""
import html
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import app
import shipment_price_backfill as bulk
from tests import test_reconciliation as fixtures


class ShipmentPriceBulkTests(fixtures.ReconciliationDomainTestCase):
    def setUp(self):
        super().setUp()
        self.original_config = {key: app.app.config[key] for key in ("TESTING", "SECRET_KEY")}
        app.app.config.update(TESTING=True, SECRET_KEY="bulk-test-only")
        self.client = app.app.test_client()
        self.login("admin")
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET unit_price_minor = 1234, currency = 'CNY'")
            conn.execute("UPDATE assembly_shipment_items SET unit_price_minor = NULL, price_recorded_at = ''")
            self.zero = self.create_shipment(conn, self.order_a, 1, 0, "CNY", "2026-09-04", "")

    def tearDown(self):
        app.app.config.update(self.original_config)
        super().tearDown()

    def login(self, username):
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = username
            session["shipment_price_csrf_token"] = "bulk-csrf"

    def preview(self, **filters):
        response = self.client.post("/admin/shipped-orders/prices/bulk/preview", data={
            "csrf_token": "bulk-csrf", **filters,
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        match = re.search(r'name="token" value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return html.unescape(match.group(1)), body

    def apply(self, token, **extra):
        return self.client.post("/admin/shipped-orders/prices/bulk/apply", data={
            "token": token, "csrf_token": "bulk-csrf", **extra,
        })

    def row(self, source_id, table="product_order_shipments"):
        with app.get_db() as conn:
            return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (source_id,)).fetchone())

    def test_preview_only_then_apply_fills_null_preserves_zero_and_audits_repeat(self):
        token, body = self.preview(customer="客户A")
        self.assertIn("当前价不等于历史成交价", body)
        self.assertIn("12.34", body)
        self.assertIn("CNY", body)
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])
        response = self.apply(token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response.location).query), {"customer": ["客户A"]})
        updated = self.row(self.ordinary_a_unpriced)
        self.assertEqual(updated["unit_price_minor"], 1234)
        self.assertEqual(updated["price_recorded_by"], "admin")
        self.assertTrue(updated["price_recorded_at"])
        self.assertEqual(self.row(self.zero)["unit_price_minor"], 0)
        self.assertEqual(self.row(self.assembly_a_cny, "assembly_shipment_items")["unit_price_minor"], 1234)
        self.assertEqual(self.apply(token).status_code, 302)
        self.assertEqual(self.row(self.ordinary_a_unpriced), updated)
        with app.get_db() as conn:
            rows = conn.execute("SELECT * FROM shipment_price_backfill_audit ORDER BY source_type").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["source_type"] for row in rows}, {"ordinary", "assembly_item"})
            self.assertTrue(all(row["unit_price_minor"] == 1234 and row["currency"] == "CNY"
                                and row["reason"] == "按产品现价补齐" for row in rows))

    def test_existing_finance_and_reconciliation_claims_are_skipped(self):
        with app.get_db() as conn:
            app.create_finance_invoice(conn, self.customer_a, [("ordinary", self.ordinary_a_cny)], "finance")
            app.create_reconciliation_statement(conn, [("ordinary", self.ordinary_a_usd)], "finance")
            conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id IN (?, ?)",
                         (self.ordinary_a_cny, self.ordinary_a_usd))
        token, body = self.preview()
        self.assertIn("开票", body)
        self.assertIn("对账", body)
        self.assertEqual(self.apply(token).status_code, 302)
        self.assertIsNone(self.row(self.ordinary_a_cny)["unit_price_minor"])
        self.assertIsNone(self.row(self.ordinary_a_usd)["unit_price_minor"])

    def test_new_claim_after_preview_rejects_whole_operation(self):
        for claim in ("finance", "reconciliation"):
            with self.subTest(claim=claim):
                token, _ = self.preview()
                with app.get_db() as conn:
                    conn.execute("UPDATE product_order_shipments SET unit_price_minor = 100 WHERE id = ?", (self.ordinary_a_unpriced,))
                    refs = [("ordinary", self.ordinary_a_unpriced)]
                    if claim == "finance":
                        claim_id = app.create_finance_invoice(conn, self.customer_a, refs, "finance")
                    else:
                        claim_id = app.create_reconciliation_statement(conn, refs, "finance")["statement_id"]
                    conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id = ?", (self.ordinary_a_unpriced,))
                self.assertEqual(self.apply(token).status_code, 409)
                self.assertIsNone(self.row(self.assembly_a_cny, "assembly_shipment_items")["unit_price_minor"])
                with app.get_db() as conn:
                    if claim == "finance":
                        app.delete_pending_finance_invoice(conn, claim_id)
                    else:
                        app.void_reconciliation_statement(conn, claim_id, "test", "finance")

    def test_changed_price_currency_quantity_or_source_set_rejects_stale_preview(self):
        mutations = [
            "UPDATE manuals SET unit_price_minor = 4321",
            "UPDATE manuals SET currency = 'USD'",
            "UPDATE product_order_shipments SET shipped_quantity = shipped_quantity + 1",
            "UPDATE assembly_shipment_items SET id = id + 1000",
        ]
        for statement in mutations:
            token, _ = self.preview()
            with app.get_db() as conn:
                conn.execute(statement)
            self.assertEqual(self.apply(token).status_code, 409)
            self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])

    def test_saved_zero_after_preview_is_never_overwritten(self):
        token, _ = self.preview()
        with app.get_db() as conn:
            conn.execute("UPDATE product_order_shipments SET unit_price_minor = 0 WHERE id = ?", (self.ordinary_a_unpriced,))
        self.assertEqual(self.apply(token).status_code, 409)
        self.assertEqual(self.row(self.ordinary_a_unpriced)["unit_price_minor"], 0)
        self.assertIsNone(self.row(self.assembly_a_cny, "assembly_shipment_items")["unit_price_minor"])

    def test_missing_unpriced_invalid_product_and_mixed_currencies(self):
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET currency = 'JPY', unit_price_minor = 88 WHERE id = ?", (self.manual_b,))
            conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id = ?", (self.ordinary_b_cny,))
        token, body = self.preview()
        self.assertIn("JPY", body)
        self.assertIn("CNY", body)
        self.assertEqual(self.apply(token).status_code, 302)
        self.assertEqual(self.row(self.ordinary_b_cny)["unit_price_minor"], 88)
        for mutation, reason in [
            ("UPDATE manuals SET unit_price_minor = NULL", "产品未定价"),
            ("UPDATE manuals SET unit_price_minor = -1", "产品价格无效"),
            ("DELETE FROM manuals", "产品不存在"),
        ]:
            with app.get_db() as conn:
                conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id = ?", (self.ordinary_a_unpriced,))
                conn.execute(mutation)
            token, body = self.preview()
            self.assertIn(reason, body)
            self.assertEqual(self.apply(token).status_code, 302)
            self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])

    def test_filters_match_list_and_are_bound_to_token_not_apply_form(self):
        token, _ = self.preview(q="SO-A", customer="客户A", shipped_at="2026-09-04")
        response = self.apply(token, q="SO-B", customer="客户B", shipped_at="2020")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlparse(response.location).query), {
            "q": ["SO-A"], "customer": ["客户A"], "shipped_at": ["2026-09-04"],
        })
        self.assertEqual(self.row(self.ordinary_a_unpriced)["unit_price_minor"], 1234)
        self.assertIsNone(self.row(self.assembly_a_cny, "assembly_shipment_items")["unit_price_minor"])

    def test_csrf_signature_and_operator_binding(self):
        token, _ = self.preview()
        self.assertEqual(self.apply(token, csrf_token="wrong").status_code, 403)
        self.assertEqual(self.apply("broken").status_code, 400)
        with app.get_db() as conn:
            conn.execute("INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES ('other-admin', 'hash', 'admin', ?, ?)", (self.now, self.now))
        self.login("other-admin")
        self.assertEqual(self.apply(token).status_code, 403)
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])

    def test_both_permissions_required_and_get_never_backfills(self):
        token, _ = self.preview()
        for manage, view in [(1, 0), (0, 1)]:
            with app.get_db() as conn:
                conn.execute("DELETE FROM users WHERE username = 'restricted'")
                conn.execute("""INSERT INTO users (username, password_hash, role, can_manage_shipped,
                             can_view_shipped, can_view_prices, created_at, updated_at)
                             VALUES ('restricted', 'hash', 'operator', ?, 1, ?, ?, ?)""", (manage, view, self.now, self.now))
            self.login("restricted")
            response = self.client.post("/admin/shipped-orders/prices/bulk/preview", data={"csrf_token": "bulk-csrf"})
            self.assertIn(response.status_code, (302, 403))
            self.assertNotIn("12.34", response.get_data(as_text=True))
            self.assertIn(self.apply(token).status_code, (302, 403))
            page = self.client.get("/admin/shipped-orders").get_data(as_text=True)
            self.assertNotIn("prices/bulk/preview", page)
        self.login("admin")
        self.assertEqual(self.client.get("/admin/shipped-orders").status_code, 200)
        self.assertEqual(self.client.get("/admin/shipped-orders/prices/bulk/apply").status_code, 405)
        app.init_db()
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])

    def test_current_zero_price_is_valid_and_totals_are_per_currency(self):
        with app.get_db() as conn:
            conn.execute("UPDATE manuals SET unit_price_minor = 0 WHERE id = ?", (self.manual_a,))
            conn.execute("UPDATE manuals SET unit_price_minor = 88, currency = 'JPY' WHERE id = ?", (self.manual_b,))
            conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id = ?", (self.ordinary_b_cny,))
            plan = bulk.build_plan(conn, [("ordinary", self.ordinary_a_unpriced),
                                         ("ordinary", self.ordinary_b_cny)], "admin")
            self.assertEqual(plan["totals"], {"CNY": 0, "JPY": 264})
        token, _ = self.preview()
        self.assertEqual(self.apply(token).status_code, 302)
        self.assertEqual(self.row(self.ordinary_a_unpriced)["unit_price_minor"], 0)

    def test_domain_requires_transaction_does_not_commit_and_returns_zero_on_replay(self):
        def assert_mutable(conn, refs):
            app.assert_finance_sources_mutable(conn, refs)
            app.assert_reconciliation_sources_mutable(conn, refs)

        with app.get_db() as conn:
            plan = bulk.build_plan(conn, [("ordinary", self.ordinary_a_unpriced)], "admin")
            with self.assertRaisesRegex(ValueError, "事务"):
                bulk.apply_plan(conn, plan, "admin", assert_mutable)
            conn.execute("BEGIN IMMEDIATE")
            self.assertEqual(bulk.apply_plan(conn, plan, "admin", assert_mutable)["updated_count"], 1)
            second_run = bulk.apply_plan(conn, plan, "admin", assert_mutable)
            self.assertEqual(second_run["updated_count"], 0)
            conn.rollback()
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_audit").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_runs").fetchone()[0], 0)

    def test_mid_batch_database_failure_rolls_back_all_prices_and_audits(self):
        token, _ = self.preview()
        with app.get_db() as conn:
            # Assembly sorts first, so ordinary audit failure happens after a prior row was written.
            conn.execute("""CREATE TRIGGER fail_bulk_audit BEFORE INSERT ON shipment_price_backfill_audit
                            WHEN NEW.source_type = 'ordinary'
                            BEGIN SELECT RAISE(ABORT, 'test audit failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test audit failure"):
            self.apply(token)
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])
        self.assertIsNone(self.row(self.assembly_a_cny, "assembly_shipment_items")["unit_price_minor"])
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_audit").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_runs").fetchone()[0], 0)

    def test_both_authoritative_assertions_run_in_transaction_before_any_price_write(self):
        for kind in ("finance", "reconciliation"):
            token, _ = self.preview()
            original = getattr(app, f"assert_{kind}_sources_mutable")

            def claim_at_assertion(conn, refs):
                self.assertTrue(conn.in_transaction)
                self.assertIsNone(conn.execute("SELECT unit_price_minor FROM assembly_shipment_items WHERE id = ?",
                                               (self.assembly_a_cny,)).fetchone()[0])
                conn.execute("UPDATE product_order_shipments SET unit_price_minor = 100 WHERE id = ?", (self.ordinary_a_unpriced,))
                source = [("ordinary", self.ordinary_a_unpriced)]
                if kind == "finance":
                    app.create_finance_invoice(conn, self.customer_a, source, "finance")
                else:
                    app.create_reconciliation_statement(conn, source, "finance")
                conn.execute("UPDATE product_order_shipments SET unit_price_minor = NULL WHERE id = ?", (self.ordinary_a_unpriced,))
                original(conn, refs)

            # Inject a real claim at the precise assertion boundary; no fake claim or assertion result.
            with patch.object(app, f"assert_{kind}_sources_mutable", side_effect=claim_at_assertion):
                self.assertEqual(self.apply(token).status_code, 409)
            self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])
            with app.get_db() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_audit").fetchone()[0], 0)

    def test_two_simultaneous_applies_update_once(self):
        token, _ = self.preview()

        def apply_from_independent_client():
            client = app.app.test_client()
            with client.session_transaction() as session:
                session.update(admin_logged_in=True, admin_username="admin", shipment_price_csrf_token="bulk-csrf")
            return client.post("/admin/shipped-orders/prices/bulk/apply",
                               data={"token": token, "csrf_token": "bulk-csrf"}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: apply_from_independent_client(), range(2)))
        self.assertEqual(statuses, [302, 302])
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_audit").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_runs").fetchone()[0], 1)

    def test_large_filtered_source_set_is_not_silently_truncated(self):
        with app.get_db() as conn:
            for _ in range(1100):
                self.create_shipment(conn, self.order_a, 1, None, "CNY", "2026-09-06", "")
        token, _ = self.preview(shipped_at="2026-09-06")
        self.assertEqual(self.apply(token).status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM shipment_price_backfill_audit").fetchone()[0], 1100)
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])

    def test_expired_preview_and_preview_csrf_are_rejected_without_writes(self):
        self.assertEqual(self.client.post("/admin/shipped-orders/prices/bulk/preview").status_code, 403)
        token, _ = self.preview()
        payload = app.shipment_price_preview_serializer().loads(token)
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=1):
            token = app.shipment_price_preview_serializer().dumps(payload)
        self.assertEqual(self.apply(token).status_code, 400)
        self.assertIsNone(self.row(self.ordinary_a_unpriced)["unit_price_minor"])
