import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app


class ProductBatchAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.original_manuals_dir = app.MANUALS_DIR
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.MANUALS_DIR = Path(self.tmpdir.name) / "manuals"
        app.MANUALS_DIR.mkdir()
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"

        self.product_a = self.create_product("PART-A", "短名称A", "客户A")
        self.product_b = self.create_product("PART-B", "短名称B", "客户A")

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.MANUALS_DIR = self.original_manuals_dir
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def create_product(self, drawing_no, product_name, customer):
        now = "2026-09-03T20:00:00"
        with app.get_db() as conn:
            return conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, model, category,
                    version, remark, filename, original_filename,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, '', '', '', '', '', '', ?, ?)
                """,
                (drawing_no, product_name, customer, now, now),
            ).lastrowid

    def configure_component(self, manual_id, drawing_no, quantity, sort_order=0):
        now = "2026-09-03T20:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO product_assembly_components (
                    manual_id, assembly_drawing_no, quantity_per_set,
                    sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (manual_id, drawing_no, quantity, sort_order, now, now),
            )

    def attach_file(self, manual_id, filename):
        now = "2026-09-03T20:00:00"
        (app.MANUALS_DIR / filename).write_bytes(filename.encode("utf-8"))
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO manual_files (
                    manual_id, filename, original_filename, file_type, created_at
                ) VALUES (?, ?, ?, 'file', ?)
                """,
                (manual_id, filename, filename, now),
            )

    def create_claimed_shipment(self, manual_id, claim_kind):
        now = "2026-09-03T20:00:00"
        with app.get_db() as conn:
            customer = conn.execute(
                "SELECT customer FROM manuals WHERE id = ?", (manual_id,)
            ).fetchone()["customer"]
            app.ensure_customer_exists(conn, customer, now)
            customer_id = conn.execute(
                "SELECT id FROM customers WHERE name = ?", (customer,)
            ).fetchone()["id"]
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    assembly_drawing_no, assembly_set_quantity, planned_ship_at,
                    created_at, updated_at
                ) VALUES (?, ?, '2026-09-01', 2, ?, '', 0, '2026-09-02', ?, ?)
                """,
                (manual_id, f"ORDER-{manual_id}", customer, now, now),
            ).lastrowid
            shipment_id = conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at,
                    unit_price_minor, currency
                ) VALUES (?, 2, '2026-09-02', ?, 100, 'CNY')
                """,
                (order_id, now),
            ).lastrowid
            if claim_kind == "finance":
                app.create_finance_invoice(
                    conn, customer_id, [("ordinary", shipment_id)], "admin"
                )
            else:
                app.create_reconciliation_statement(
                    conn, [("ordinary", shipment_id)], "admin"
                )

    def assert_products_exist(self, *manual_ids):
        with app.get_db() as conn:
            remaining = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM manuals WHERE id IN ({','.join('?' for _ in manual_ids)})",
                    manual_ids,
                ).fetchall()
            }
        self.assertEqual(remaining, set(manual_ids))

    def assert_product_redirect_filters(self, response):
        location = urlsplit(response.location)
        self.assertEqual(location.path, "/admin/products")
        self.assertEqual(
            parse_qs(location.query),
            {
                "q": ["needle"],
                "supplier": ["SPEC"],
                "customer": ["客户A"],
                "sort": ["drawing_no"],
                "direction": ["asc"],
            },
        )

    def test_batch_setting_adds_or_updates_assembly_and_preserves_other_memberships(self):
        self.configure_component(self.product_a, "ASM-OLD", 9, 0)
        self.configure_component(self.product_a, "Asm-100", 1, 1)

        response = self.client.post(
            "/admin/products/assembly-components/batch",
            data={
                "manual_id": [str(self.product_a), str(self.product_b)],
                "assembly_drawing_no": "ASM-100",
                "quantity_per_set": "4",
            },
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            rows = conn.execute(
                """
                SELECT manual_id, assembly_drawing_no, quantity_per_set
                FROM product_assembly_components
                ORDER BY manual_id, sort_order, id
                """
            ).fetchall()
        self.assertEqual(
            [
                (row["manual_id"], row["assembly_drawing_no"], row["quantity_per_set"])
                for row in rows
            ],
            [
                (self.product_a, "ASM-OLD", 9),
                (self.product_a, "ASM-100", 4),
                (self.product_b, "ASM-100", 4),
            ],
        )

    def test_batch_setting_rejects_products_from_different_customers_atomically(self):
        product_c = self.create_product("PART-C", "短名称C", "客户B")

        response = self.client.post(
            "/admin/products/assembly-components/batch",
            data={
                "manual_id": [str(self.product_a), str(product_c)],
                "assembly_drawing_no": "ASM-MIXED",
                "quantity_per_set": "2",
            },
            follow_redirects=True,
        )

        self.assertIn("只能批量设置同一客户的产品", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM product_assembly_components
                WHERE assembly_drawing_no = 'ASM-MIXED'
                """
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_batch_setting_matches_unicode_case_variants_without_creating_duplicate(self):
        self.configure_component(self.product_a, "ÄSM-100", 1, 0)

        response = self.client.post(
            "/admin/products/assembly-components/batch",
            data={
                "manual_id": str(self.product_a),
                "assembly_drawing_no": "äsm-100",
                "quantity_per_set": "5",
            },
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            rows = conn.execute(
                """
                SELECT assembly_drawing_no, quantity_per_set
                FROM product_assembly_components
                WHERE manual_id = ?
                ORDER BY id
                """,
                (self.product_a,),
            ).fetchall()
        self.assertEqual(
            [(row["assembly_drawing_no"], row["quantity_per_set"]) for row in rows],
            [("äsm-100", 5)],
        )

    def test_batch_delete_rejects_unknown_id_and_duplicate_selection_atomically(self):
        self.attach_file(self.product_a, "part-a.pdf")

        response = self.client.post(
            "/admin/products/delete-batch",
            data={
                "manual_id": [
                    str(self.product_a),
                    str(self.product_a),
                    str(self.product_b),
                    "999999",
                ]
            },
            follow_redirects=True,
        )

        self.assertIn("整批未删除", response.get_data(as_text=True))
        self.assert_products_exist(self.product_a, self.product_b)
        self.assertTrue((app.MANUALS_DIR / "part-a.pdf").exists())

    def test_batch_delete_rejects_finance_or_reconciliation_locked_product_atomically(self):
        for claim_kind in ("finance", "reconciliation"):
            with self.subTest(claim_kind=claim_kind):
                unlocked_id = self.create_product(
                    f"FREE-{claim_kind}", "普通产品", "客户A"
                )
                locked_id = self.create_product(
                    f"LOCK-{claim_kind}", "锁定产品", "客户A"
                )
                self.create_claimed_shipment(locked_id, claim_kind)

                response = self.client.post(
                    "/admin/products/delete-batch",
                    data={"manual_id": [str(unlocked_id), str(locked_id)]},
                    follow_redirects=True,
                )

                self.assertIn("整批未删除", response.get_data(as_text=True))
                self.assert_products_exist(unlocked_id, locked_id)

    def test_batch_delete_restores_quarantined_files_when_database_commit_fails(self):
        self.attach_file(self.product_a, "part-a.pdf")
        with app.get_db() as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_product_delete
                BEFORE DELETE ON manuals
                BEGIN
                    SELECT RAISE(ABORT, 'forced delete failure');
                END
                """
            )

        response = self.client.post(
            "/admin/products/delete-batch",
            data={"manual_id": [str(self.product_a), str(self.product_b)]},
            follow_redirects=True,
        )

        self.assertIn("整批未删除", response.get_data(as_text=True))
        self.assert_products_exist(self.product_a, self.product_b)
        self.assertTrue((app.MANUALS_DIR / "part-a.pdf").exists())

    def test_batch_delete_deduplicates_ids_and_removes_all_related_data_and_files(self):
        self.attach_file(self.product_a, "part-a.pdf")
        self.attach_file(self.product_b, "part-b.pdf")
        self.configure_component(self.product_a, "ASM-DELETE", 2)
        now = "2026-09-03T20:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO product_materials (
                    manual_id, material, thickness, surface_type, supplier,
                    sort_order, created_at, updated_at
                ) VALUES (?, '铝板', '2mm', '喷涂', '', 0, ?, ?)
                """,
                (self.product_b, now, now),
            )

        response = self.client.post(
            "/admin/products/delete-batch",
            data={
                "manual_id": [
                    str(self.product_a), str(self.product_a), str(self.product_b)
                ],
                "return_q": "needle",
                "return_supplier": "SPEC",
                "return_customer": "客户A",
                "return_sort": "drawing_no",
                "return_direction": "asc",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assert_product_redirect_filters(response)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM manuals WHERE id IN (?, ?)",
                    (self.product_a, self.product_b),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM manual_files").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_materials").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM product_assembly_components").fetchone()[0], 0)
        self.assertFalse((app.MANUALS_DIR / "part-a.pdf").exists())
        self.assertFalse((app.MANUALS_DIR / "part-b.pdf").exists())

    def test_single_delete_uses_product_list_redirect_and_preserves_filters(self):
        response = self.client.post(
            f"/admin/{self.product_a}/delete",
            data={
                "return_q": "needle",
                "return_supplier": "SPEC",
                "return_customer": "客户A",
                "return_sort": "drawing_no",
                "return_direction": "asc",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assert_product_redirect_filters(response)

    def test_read_only_user_has_separate_column_widths_and_cannot_batch_edit(self):
        now = "2026-09-03T20:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_manage_products, can_edit_products,
                    created_at, updated_at
                )
                VALUES ('product-viewer', 'unused', 'operator', 1, 1, 0, ?, ?)
                """,
                (now, now),
            )
        with self.client.session_transaction() as session:
            session["admin_username"] = "product-viewer"
            session["admin_role"] = "operator"

        html = self.client.get("/admin/products").get_data(as_text=True)
        self.assertIn("factory-web.product-column-widths.v2.readonly", html)
        self.assertNotIn('<input type="checkbox" data-product-select-all>', html)
        self.assertNotIn('data-product-assembly-batch\n', html)
        self.assertNotIn('data-product-delete-open', html)

        response = self.client.post(
            "/admin/products/assembly-components/batch",
            data={
                "manual_id": str(self.product_a),
                "assembly_drawing_no": "ASM-DENIED",
                "quantity_per_set": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM product_assembly_components
                WHERE assembly_drawing_no = 'ASM-DENIED'
                """
            ).fetchone()[0]
        self.assertEqual(count, 0)

        delete_response = self.client.post(
            "/admin/products/delete-batch",
            data={"manual_id": [str(self.product_a), str(self.product_b)]},
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assert_products_exist(self.product_a, self.product_b)

    def test_product_list_displays_batch_controls_and_searches_assembly_drawing(self):
        self.configure_component(self.product_a, "ASM-100", 2, 0)
        self.configure_component(self.product_a, "ASM-200", 3, 1)

        html = self.client.get("/admin/products").get_data(as_text=True)
        self.assertIn('data-product-select-all', html)
        self.assertIn('data-product-assembly-batch', html)
        self.assertIn('data-product-delete-open', html)
        self.assertIn('data-product-delete-dialog', html)
        self.assertIn('data-product-delete-list', html)
        self.assertIn('data-product-delete-count', html)
        self.assertIn('/admin/products/delete-batch?', html)
        self.assertIn('name="manual_id"', html)
        self.assertIn('aria-label="选择产品 PART-A 短名称A"', html)
        self.assertIn('data-product-drawing-no="PART-A"', html)
        self.assertIn('data-product-name="短名称A"', html)
        self.assertIn('data-product-customer="客户A"', html)
        self.assertIn("所属组装件", html)
        self.assertIn("ASM-100 × 2", html)
        self.assertIn("ASM-200 × 3", html)

        search_html = self.client.get(
            "/admin/products", query_string={"q": "ASM-200"}
        ).get_data(as_text=True)
        self.assertIn("PART-A", search_html)
        self.assertNotIn("PART-B", search_html)
        self.assertIn(
            "/admin/products/delete-batch?q=ASM-200&amp;", search_html
        )

    def test_product_batch_script_updates_selection_and_confirms_submission(self):
        script = textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            class Element {
              constructor() {
                this.checked = false;
                this.indeterminate = false;
                this.disabled = false;
                this.textContent = "";
                this.listeners = {};
              }
              addEventListener(type, listener) {
                (this.listeners[type] ||= []).push(listener);
              }
              emit(type) {
                let prevented = false;
                const event = {preventDefault: () => { prevented = true; }};
                for (const listener of this.listeners[type] || []) listener(event);
                return prevented;
              }
            }

            const selectAll = new Element();
            const first = new Element();
            const second = new Element();
            const count = new Element();
            const submitButton = new Element();
            const deleteButton = new Element();
            const deleteDialog = new Element();
            deleteDialog.open = false;
            deleteDialog.showModal = () => { deleteDialog.open = true; };
            const deleteList = new Element();
            deleteList.children = [];
            deleteList.replaceChildren = (...children) => { deleteList.children = children; };
            const deleteInputs = new Element();
            deleteInputs.children = [];
            deleteInputs.replaceChildren = (...children) => { deleteInputs.children = children; };
            const deleteCount = new Element();
            const form = new Element();
            form.elements = {
              assembly_drawing_no: {value: "ASM-100"},
              quantity_per_set: {value: "2"},
            };
            form.querySelector = (selector) => ({
              "[data-product-selection-count]": count,
              "[data-product-assembly-submit]": submitButton,
              "[data-product-delete-open]": deleteButton,
            })[selector] || null;
            first.value = "11";
            first.dataset = {
              productDrawingNo: "PART-A",
              productName: "产品 A",
              productCustomer: "客户 A",
              productSpecification: "规格 A",
            };
            second.value = "12";
            second.dataset = {
              productDrawingNo: "PART-B",
              productName: "产品 B",
              productCustomer: "客户 B",
              productSpecification: "规格 B",
            };

            global.document = {
              querySelector: (selector) => ({
                "[data-product-assembly-batch]": form,
                "[data-product-select-all]": selectAll,
                "[data-product-delete-dialog]": deleteDialog,
                "[data-product-delete-list]": deleteList,
                "[data-product-delete-inputs]": deleteInputs,
                "[data-product-delete-count]": deleteCount,
              })[selector] || null,
              querySelectorAll: (selector) => selector === "[data-product-row-select]" ? [first, second] : [],
              createElement: () => new Element(),
            };
            let confirmation = "";
            global.window = {confirm: (message) => { confirmation = message; return true; }};
            vm.runInThisContext(fs.readFileSync("static/product_list.js", "utf8"));

            assert.equal(count.textContent, "已选择 0 个产品");
            assert.equal(submitButton.disabled, true);
            assert.equal(deleteButton.disabled, true);

            first.checked = true;
            first.emit("change");
            assert.equal(count.textContent, "已选择 1 个产品");
            assert.equal(selectAll.indeterminate, true);
            assert.equal(submitButton.disabled, false);
            assert.equal(deleteButton.disabled, false);

            selectAll.checked = true;
            selectAll.emit("change");
            assert.equal(first.checked, true);
            assert.equal(second.checked, true);
            assert.equal(count.textContent, "已选择 2 个产品");

            assert.equal(form.emit("submit"), false);
            assert.match(confirmation, /2 个产品.*ASM-100.*每套 2 个/);

            deleteButton.emit("click");
            assert.equal(deleteDialog.open, true);
            assert.equal(deleteCount.textContent, "2");
            assert.equal(deleteList.children.length, 2);
            assert.match(deleteList.children[0].textContent, /PART-A.*产品 A.*客户 A.*规格 A/);
            assert.deepEqual(deleteInputs.children.map((input) => input.value), ["11", "12"]);
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mobile_product_selection_cell_uses_full_width_card_layout(self):
        css = (app.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn(
            """.product-table {
    width: 100%;
    min-width: 0;""",
            css,
        )
        self.assertIn(
            """.product-table .shipment-select-cell input {
    justify-self: start;""",
            css,
        )
        self.assertIn(
            """.product-table .shipment-select-cell {
    display: grid;
    grid-template-columns: 86px minmax(0, 1fr);
    width: 100%;
    min-width: 0;
    text-align: left;""",
            css,
        )


if __name__ == "__main__":
    unittest.main()
