import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import app


class ProductBatchAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
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

    def test_product_list_displays_batch_controls_and_searches_assembly_drawing(self):
        self.configure_component(self.product_a, "ASM-100", 2, 0)
        self.configure_component(self.product_a, "ASM-200", 3, 1)

        html = self.client.get("/admin/products").get_data(as_text=True)
        self.assertIn('data-product-select-all', html)
        self.assertIn('data-product-assembly-batch', html)
        self.assertIn('name="manual_id"', html)
        self.assertIn('aria-label="选择产品 PART-A 短名称A"', html)
        self.assertIn("所属组装件", html)
        self.assertIn("ASM-100 × 2", html)
        self.assertIn("ASM-200 × 3", html)

        search_html = self.client.get(
            "/admin/products", query_string={"q": "ASM-200"}
        ).get_data(as_text=True)
        self.assertIn("PART-A", search_html)
        self.assertNotIn("PART-B", search_html)

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
            const form = new Element();
            form.elements = {
              assembly_drawing_no: {value: "ASM-100"},
              quantity_per_set: {value: "2"},
            };
            form.querySelector = (selector) => ({
              "[data-product-selection-count]": count,
              "[data-product-assembly-submit]": submitButton,
            })[selector] || null;

            global.document = {
              querySelector: (selector) => ({
                "[data-product-assembly-batch]": form,
                "[data-product-select-all]": selectAll,
              })[selector] || null,
              querySelectorAll: (selector) => selector === "[data-product-row-select]" ? [first, second] : [],
            };
            let confirmation = "";
            global.window = {confirm: (message) => { confirmation = message; return true; }};
            vm.runInThisContext(fs.readFileSync("static/product_list.js", "utf8"));

            assert.equal(count.textContent, "已选择 0 个产品");
            assert.equal(submitButton.disabled, true);

            first.checked = true;
            first.emit("change");
            assert.equal(count.textContent, "已选择 1 个产品");
            assert.equal(selectAll.indeterminate, true);
            assert.equal(submitButton.disabled, false);

            selectAll.checked = true;
            selectAll.emit("change");
            assert.equal(first.checked, true);
            assert.equal(second.checked, true);
            assert.equal(count.textContent, "已选择 2 个产品");

            assert.equal(form.emit("submit"), false);
            assert.match(confirmation, /2 个产品.*ASM-100.*每套 2 个/);
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
