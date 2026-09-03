import subprocess
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

import app


class OrderEntryPageTests(unittest.TestCase):
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

        with app.get_db() as conn:
            now = "2026-09-03T12:00:00"
            self.product_a = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, model, category,
                    version, remark, filename, original_filename,
                    created_at, updated_at
                )
                VALUES ('PART-001', '左侧支架', '客户A', '', '', '', '', '', '', ?, ?)
                """,
                (now, now),
            ).lastrowid

    def create_product(self, drawing_no, product_name, customer="客户A"):
        with app.get_db() as conn:
            now = "2026-09-03T12:00:00"
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

    def configure_component(self, manual_id, assembly_drawing_no, quantity_per_set):
        with app.get_db() as conn:
            now = "2026-09-03T12:00:00"
            conn.execute(
                """
                INSERT INTO product_assembly_components (
                    manual_id, assembly_drawing_no, quantity_per_set,
                    sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (manual_id, assembly_drawing_no, quantity_per_set, now, now),
            )

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def test_new_order_offers_product_name_selection_and_one_delivery_date(self):
        response = self.client.get("/admin/orders/new")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-product-drawing-select', html)
        self.assertIn('data-product-name-select', html)
        self.assertIn('data-order-delivery-date', html)
        self.assertIn('左侧支架 / PART-001', html)

    def test_assembly_order_persists_assembly_metadata_on_every_component(self):
        product_b = self.create_product("PART-002", "右侧支架")
        self.configure_component(self.product_a, "ASM-100", 2)
        self.configure_component(product_b, "ASM-100", 3)

        with app.get_db() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(product_orders)").fetchall()
            }
        self.assertIn("assembly_drawing_no", columns)
        self.assertIn("assembly_set_quantity", columns)

        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-001",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "10",
                "manual_id": [str(self.product_a), str(product_b)],
                "quantity": ["20", "30"],
                "planned_ship_at": ["2026-10-01", "2026-10-01"],
                "material_stock_status": ["", ""],
                "remark": ["", ""],
                "carton_status": ["", ""],
                "recent_ship_status": ["", ""],
            },
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            rows = conn.execute(
                """
                SELECT manual_id, quantity, assembly_drawing_no,
                       assembly_set_quantity
                FROM product_orders
                WHERE order_no = 'SO-ASM-001'
                ORDER BY manual_id
                """
            ).fetchall()
        self.assertEqual(
            [
                (
                    row["manual_id"],
                    row["quantity"],
                    row["assembly_drawing_no"],
                    row["assembly_set_quantity"],
                )
                for row in rows
            ],
            [
                (self.product_a, 20, "ASM-100", 10),
                (product_b, 30, "ASM-100", 10),
            ],
        )

    def test_assembly_order_rejects_non_positive_set_quantity(self):
        self.configure_component(self.product_a, "ASM-100", 2)

        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-ZERO",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "0",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("组装数量必须大于 0", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-ASM-ZERO'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_assembly_order_rejects_set_quantity_above_limit(self):
        self.configure_component(self.product_a, "ASM-100", 2)

        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-TOO-LARGE",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "2147483648",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("组装数量不能超过 2147483647", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-ASM-TOO-LARGE'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_new_order_rejects_product_quantity_above_limit(self):
        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-QTY-TOO-LARGE",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "",
                "assembly_set_quantity": "",
                "manual_id": str(self.product_a),
                "quantity": "2147483648",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("订单数量不能超过 2147483647", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-QTY-TOO-LARGE'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_order_import_rejects_single_and_aggregate_quantity_above_limit(self):
        with self.assertRaises(OverflowError):
            app.parse_import_quantity(2_147_483_648)

        with app.get_db() as conn:
            items, errors = app.match_order_import_items(
                conn,
                [
                    {
                        "row_number": 2,
                        "drawing_no": "PART-001",
                        "quantity": 2_147_483_647,
                        "planned_ship_at": "",
                    },
                    {
                        "row_number": 3,
                        "drawing_no": "PART-001",
                        "quantity": 1,
                        "planned_ship_at": "",
                    },
                ],
            )

        self.assertEqual(items, [])
        self.assertTrue(
            any("合计数量不能超过 2147483647" in error for error in errors),
            errors,
        )

    def test_assembly_order_rejects_unknown_assembly_for_customer(self):
        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-UNKNOWN",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-NOT-CONFIGURED",
                "assembly_set_quantity": "10",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("请选择该客户有效的组装图号", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-ASM-UNKNOWN'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_assembly_order_rejects_products_from_another_customer(self):
        other_customer_product = self.create_product(
            "PART-B-001", "其他客户支架", customer="客户B"
        )
        self.configure_component(self.product_a, "ASM-100", 2)

        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-TAMPERED",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "10",
                "manual_id": str(other_customer_product),
                "quantity": "10",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("订单产品必须属于所选客户", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-ASM-TAMPERED'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_new_order_requires_a_customer(self):
        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-NO-CUSTOMER",
                "ordered_at": "2026-09-03",
                "customer": "",
                "assembly_drawing_no": "",
                "assembly_set_quantity": "",
                "manual_id": str(self.product_a),
                "quantity": "10",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("请选择客户", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-NO-CUSTOMER'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_assembly_quantity_requires_an_assembly_drawing(self):
        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-PARTIAL",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "",
                "assembly_set_quantity": "10",
                "manual_id": str(self.product_a),
                "quantity": "10",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn("组装图号和组装数量必须同时填写", response.get_data(as_text=True))
        with app.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM product_orders WHERE order_no = 'SO-ASM-PARTIAL'"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_order_assembly_api_filters_options_and_returns_definition(self):
        product_b = self.create_product("PART-002", "右侧支架")
        other_product = self.create_product("OTHER-001", "其他客户零件", customer="客户B")
        self.configure_component(self.product_a, "ASM-100", 2)
        self.configure_component(product_b, "ASM-100", 3)
        self.configure_component(other_product, "ASM-OTHER", 9)

        options_response = self.client.get(
            "/admin/orders/assembly-options", query_string={"customer": "客户A"}
        )
        self.assertEqual(options_response.status_code, 200)
        self.assertEqual(
            options_response.get_json(),
            {"assembly_drawing_numbers": ["ASM-100"]},
        )

        definition_response = self.client.get(
            "/admin/orders/assembly-definition",
            query_string={
                "customer": "客户A",
                "assembly_drawing_no": "asm-100",
            },
        )
        self.assertEqual(definition_response.status_code, 200)
        self.assertEqual(
            definition_response.get_json(),
            {
                "assembly_drawing_no": "ASM-100",
                "items": [
                    {
                        "manual_id": self.product_a,
                        "drawing_no": "PART-001",
                        "product_name": "左侧支架",
                        "quantity_per_set": 2,
                    },
                    {
                        "manual_id": product_b,
                        "drawing_no": "PART-002",
                        "product_name": "右侧支架",
                        "quantity_per_set": 3,
                    },
                ],
            },
        )

    def test_new_order_page_has_assembly_order_controls(self):
        response = self.client.get("/admin/orders/new")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="customer"', html)
        self.assertIn('data-order-customer-filter', html)
        self.assertIn('name="assembly_drawing_no"', html)
        self.assertIn('data-order-assembly-drawing', html)
        self.assertIn('name="assembly_set_quantity"', html)
        self.assertIn('data-order-assembly-quantity', html)
        self.assertIn('name="assembly_set_quantity" min="1" max="2147483647"', html)
        self.assertIn('data-generate-assembly-order', html)
        self.assertIn('data-assembly-options-url=', html)
        self.assertIn('data-assembly-definition-url=', html)

    def test_orders_list_displays_and_searches_assembly_metadata(self):
        self.configure_component(self.product_a, "ASM-100", 2)
        response = self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-LIST",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "10",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        list_html = self.client.get("/admin/orders").get_data(as_text=True)
        self.assertIn("组装图号", list_html)
        self.assertIn("ASM-100", list_html)
        self.assertIn("10 套", list_html)

        matching_html = self.client.get(
            "/admin/orders", query_string={"q": "ASM-100"}
        ).get_data(as_text=True)
        self.assertIn("SO-ASM-LIST", matching_html)

        missing_html = self.client.get(
            "/admin/orders", query_string={"q": "ASM-NOT-FOUND"}
        ).get_data(as_text=True)
        self.assertNotIn("SO-ASM-LIST", missing_html)

    def test_edit_assembly_order_rejects_product_from_another_customer(self):
        other_customer_product = self.create_product(
            "PART-B-EDIT", "其他客户零件", customer="客户B"
        )
        self.configure_component(self.product_a, "ASM-100", 2)
        self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-EDIT",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "10",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
        )
        with app.get_db() as conn:
            order_id = conn.execute(
                "SELECT id FROM product_orders WHERE order_no = 'SO-ASM-EDIT'"
            ).fetchone()["id"]

        edit_html = self.client.get(
            f"/admin/orders/{order_id}/edit"
        ).get_data(as_text=True)
        self.assertIn("ASM-100", edit_html)
        self.assertIn("10 套", edit_html)

        response = self.client.post(
            f"/admin/orders/{order_id}/edit",
            data={
                "manual_id": str(other_customer_product),
                "order_no": "SO-ASM-EDIT",
                "ordered_at": "2026-09-03",
                "quantity": "20",
                "customer_email": "",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
            follow_redirects=True,
        )

        self.assertIn(
            "组装订单不能更换为其他客户的产品", response.get_data(as_text=True)
        )
        with app.get_db() as conn:
            order = conn.execute(
                """
                SELECT manual_id, customer, assembly_drawing_no,
                       assembly_set_quantity
                FROM product_orders WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
        self.assertEqual(
            (
                order["manual_id"],
                order["customer"],
                order["assembly_drawing_no"],
                order["assembly_set_quantity"],
            ),
            (self.product_a, "客户A", "ASM-100", 10),
        )

    def test_edit_product_rejects_customer_change_when_assembly_orders_exist(self):
        self.configure_component(self.product_a, "ASM-100", 2)
        self.client.post(
            "/admin/orders/new",
            data={
                "order_no": "SO-ASM-PRODUCT-CUSTOMER",
                "ordered_at": "2026-09-03",
                "customer": "客户A",
                "assembly_drawing_no": "ASM-100",
                "assembly_set_quantity": "10",
                "manual_id": str(self.product_a),
                "quantity": "20",
                "planned_ship_at": "2026-10-01",
                "material_stock_status": "",
                "remark": "",
                "carton_status": "",
                "recent_ship_status": "",
            },
        )

        response = self.client.post(
            f"/admin/{self.product_a}/edit",
            data={
                "drawing_no": "PART-001",
                "product_name": "左侧支架",
                "supplier": "",
                "customer": "客户B",
                "pack_quantity": "",
                "pack_carton_size": "",
                "pack_weight": "",
                "sku": "",
                "barcode": "",
                "default_location_id": "",
                "min_stock": "0",
                "remark": "",
                "description_html": "",
                "assembly_drawing_no": ["ASM-100"],
                "assembly_quantity_per_set": ["2"],
            },
            follow_redirects=True,
        )

        self.assertIn(
            "已有组装订单的产品不能直接更换客户", response.get_data(as_text=True)
        )
        with app.get_db() as conn:
            manual_customer = conn.execute(
                "SELECT customer FROM manuals WHERE id = ?", (self.product_a,)
            ).fetchone()["customer"]
            order_customer = conn.execute(
                """
                SELECT customer FROM product_orders
                WHERE order_no = 'SO-ASM-PRODUCT-CUSTOMER'
                """
            ).fetchone()["customer"]
        self.assertEqual(manual_customer, "客户A")
        self.assertEqual(order_customer, "客户A")

    def test_order_entry_script_uses_a_version_that_tracks_its_mtime(self):
        html = self.client.get("/admin/orders/new").get_data(as_text=True)
        match = re.search(r"/static/order_entry\.js\?v=(\d+)", html)

        self.assertIsNotNone(match)
        script_mtime = int((Path(app.BASE_DIR) / "static" / "order_entry.js").stat().st_mtime)
        self.assertGreaterEqual(int(match.group(1)), script_mtime)

    def test_product_name_selection_syncs_drawing_and_customer_filter(self):
        script = textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            function option(value, customer, drawingNo, productName) {
              return {
                value,
                hidden: false,
                disabled: false,
                dataset: {customer, drawingNo, productName},
              };
            }

            class Select {
              constructor(options) {
                this.options = options;
                this._value = "";
              }
              get value() { return this._value; }
              set value(next) { this._value = String(next); }
              get selectedOptions() {
                return this.options.filter((item) => item.value === this._value);
              }
            }

            const drawing = new Select([
              option("", "", "", ""),
              option("1", "客户A", "PART-001", "左侧支架"),
              option("2", "客户B", "PART-002", "右侧支架"),
              option("3", "客户AA", "PART-003", "加长支架"),
            ]);
            const productName = new Select([
              option("", "", "", ""),
              option("1", "客户A", "PART-001", "左侧支架"),
              option("2", "客户B", "PART-002", "右侧支架"),
              option("3", "客户AA", "PART-003", "加长支架"),
            ]);
            const customerCell = {textContent: "-"};
            const row = {
              querySelector(selector) {
                return ({
                  "[data-product-drawing-select]": drawing,
                  "[data-product-name-select]": productName,
                  "[data-customer-name-cell]": customerCell,
                })[selector] || null;
              },
            };

            global.document = {querySelector: () => null};
            vm.runInThisContext(fs.readFileSync("static/order_entry.js", "utf8"));
            const api = globalThis.__orderEntryTestApi;
            assert.ok(api, "order entry script must expose the real behavior");

            productName.value = "1";
            api.syncProductRow(row, productName);
            assert.equal(drawing.value, "1");
            assert.equal(productName.value, "1");
            assert.equal(customerCell.textContent, "客户A");

            api.filterProductRow(row, "客户b");
            assert.equal(drawing.options[1].hidden, true);
            assert.equal(productName.options[1].disabled, true);
            assert.equal(drawing.options[2].hidden, false);
            assert.equal(productName.options[2].disabled, false);
            assert.equal(drawing.value, "");
            assert.equal(productName.value, "");
            assert.equal(customerCell.textContent, "-");

            api.filterProductRow(row, "客户A");
            assert.equal(drawing.options[1].hidden, false);
            assert.equal(drawing.options[3].hidden, true);
            assert.equal(productName.options[3].disabled, true);
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assembly_helpers_populate_options_and_generate_editable_rows(self):
        script = textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            function productOption(value, customer) {
              return {value: String(value), dataset: {customer}, hidden: false, disabled: false};
            }

            class Select {
              constructor(options = []) {
                this.options = options;
                this.children = [];
                this.value = "";
                this.disabled = false;
              }
              get selectedOptions() {
                return this.options.filter((option) => option.value === this.value);
              }
              replaceChildren(...children) {
                this.children = children;
                this.options = children;
                this.value = "";
              }
              appendChild(child) {
                this.children.push(child);
                this.options.push(child);
              }
            }

            function makeRow() {
              const drawing = new Select([
                productOption("", ""),
                productOption("1", "客户A"),
                productOption("2", "客户A"),
              ]);
              const productName = new Select([
                productOption("", ""),
                productOption("1", "客户A"),
                productOption("2", "客户A"),
              ]);
              const quantity = {value: "", type: "number", checked: false};
              const plannedDate = {value: "", type: "date", checked: false};
              const customerCell = {textContent: "-"};
              const removeButton = {disabled: false};
              const row = {
                drawing,
                productName,
                quantity,
                plannedDate,
                querySelector(selector) {
                  return ({
                    "[data-product-drawing-select]": drawing,
                    "[data-product-name-select]": productName,
                    "[data-customer-name-cell]": customerCell,
                    '[name="quantity"]': quantity,
                    'input[name="planned_ship_at"]': plannedDate,
                    "[data-remove-row]": removeButton,
                  })[selector] || null;
                },
                querySelectorAll(selector) {
                  if (selector === "input") return [quantity, plannedDate];
                  return [];
                },
                cloneNode() { return makeRow().row; },
              };
              return {row, drawing, productName, quantity, plannedDate};
            }

            const createdRows = [];
            const body = {
              rows: [],
              replaceChildren() { this.rows = []; },
              appendChild(row) { this.rows.push(row); createdRows.push(row); },
              querySelectorAll(selector) {
                return selector === "[data-order-item]" ? this.rows : [];
              },
            };
            global.document = {
              querySelector: () => null,
              createElement: () => ({value: "", textContent: "", disabled: false}),
            };
            vm.runInThisContext(fs.readFileSync("static/order_entry.js", "utf8"));
            const api = globalThis.__orderEntryTestApi;

            const assemblySelect = new Select();
            api.populateAssemblyOptions(assemblySelect, ["ASM-100", "ASM-200"]);
            assert.deepEqual(assemblySelect.options.map((option) => option.value), ["", "ASM-100", "ASM-200"]);
            assert.equal(assemblySelect.disabled, false);

            const template = makeRow().row;
            api.replaceRowsWithAssembly(
              body,
              template,
              [
                {manual_id: 1, quantity_per_set: 2},
                {manual_id: 2, quantity_per_set: 3},
              ],
              10,
              "2026-10-20",
              "客户A",
            );

            assert.equal(createdRows.length, 2);
            assert.equal(createdRows[0].drawing.value, "1");
            assert.equal(createdRows[0].productName.value, "1");
            assert.equal(createdRows[0].quantity.value, "20");
            assert.equal(createdRows[1].quantity.value, "30");
            assert.equal(createdRows[1].plannedDate.value, "2026-10-20");
            assert.equal(createdRows[0].quantity.disabled, undefined);
            createdRows[0].quantity.value = "21";
            assert.equal(createdRows[0].quantity.value, "21");

            assert.throws(
              () => api.replaceRowsWithAssembly(
                body,
                template,
                [{manual_id: 1, quantity_per_set: 2147483647}],
                2,
                "2026-10-20",
                "客户A",
              ),
              /2147483647/,
            );
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assembly_generation_ignores_response_after_selection_changes(self):
        script = textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            class Element {
              constructor(value = "") {
                this.value = value;
                this.type = "text";
                this.dataset = {};
                this.listeners = {};
                this.disabled = false;
                this.checked = false;
                this.textContent = "";
              }
              addEventListener(type, listener) {
                (this.listeners[type] ||= []).push(listener);
              }
              async emit(type) {
                await Promise.all((this.listeners[type] || []).map((listener) => listener({target: this})));
              }
              matches() { return false; }
            }

            class Select extends Element {
              constructor(options = []) {
                super("");
                this.options = options;
              }
              get selectedOptions() {
                return this.options.filter((option) => option.value === this.value);
              }
              replaceChildren(...options) {
                this.options = options;
                this.value = "";
              }
            }

            function productOption(value, customer) {
              return {value: String(value), dataset: {customer}, hidden: false, disabled: false};
            }

            function makeRow() {
              const drawing = new Select([productOption("", ""), productOption("1", "客户A")]);
              const productName = new Select([productOption("", ""), productOption("1", "客户A")]);
              const quantity = new Element();
              quantity.name = "quantity";
              const plannedDate = new Element();
              plannedDate.name = "planned_ship_at";
              plannedDate.type = "date";
              const remark = new Element();
              remark.name = "remark";
              const removeButton = new Element();
              const customerCell = {textContent: "-"};
              const row = {
                querySelector(selector) {
                  return ({
                    "[data-product-drawing-select]": drawing,
                    "[data-product-name-select]": productName,
                    "[data-customer-name-cell]": customerCell,
                    '[name="quantity"]': quantity,
                    '[name="remark"]': remark,
                    'input[name="planned_ship_at"]': plannedDate,
                    "[data-remove-row]": removeButton,
                  })[selector] || null;
                },
                querySelectorAll(selector) {
                  if (selector === "input") return [quantity, plannedDate, remark];
                  if (selector === "[data-status-checkbox]") return [];
                  return [];
                },
                cloneNode() { return makeRow().row; },
              };
              return row;
            }

            const initialRow = makeRow();
            const body = new Element();
            body.rows = [initialRow];
            body.querySelector = (selector) => selector === "[data-order-item]" ? body.rows[0] : null;
            body.querySelectorAll = (selector) => selector === "[data-order-item]" ? body.rows : [];
            body.replaceChildren = () => { body.rows = []; };
            body.appendChild = (row) => body.rows.push(row);

            const addButton = new Element();
            const deliveryDate = new Element("2026-10-20");
            const customer = new Select();
            customer.value = "客户A";
            const assemblyDrawing = new Select([
              {value: "", dataset: {}},
              {value: "ASM-100", dataset: {}},
            ]);
            const assemblyQuantity = new Element();
            assemblyQuantity.type = "number";
            const generateButton = new Element();
            const status = new Element();
            const form = new Element();
            form.dataset = {
              assemblyOptionsUrl: "/admin/orders/assembly-options",
              assemblyDefinitionUrl: "/admin/orders/assembly-definition",
            };
            form.querySelector = (selector) => ({
              "[data-order-items]": body,
              "[data-add-row]": addButton,
              "[data-order-customer-filter]": customer,
              "[data-order-delivery-date]": deliveryDate,
              "[data-order-assembly-drawing]": assemblyDrawing,
              "[data-order-assembly-quantity]": assemblyQuantity,
              "[data-generate-assembly-order]": generateButton,
              "[data-assembly-order-status]": status,
            })[selector] || null;
            const root = {querySelector: (selector) => selector === "[data-order-entry]" ? form : null};

            let resolveFetch;
            global.fetch = () => new Promise((resolve) => { resolveFetch = resolve; });
            global.window = {confirm: () => true};
            global.document = {
              querySelector: () => null,
              createElement: () => ({value: "", textContent: "", disabled: false}),
            };
            vm.runInThisContext(fs.readFileSync("static/order_entry.js", "utf8"));
            globalThis.__orderEntryTestApi.initializeOrderEntry(root);

            (async () => {
              assemblyDrawing.value = "ASM-100";
              await assemblyDrawing.emit("change");
              assemblyQuantity.value = "10";
              await assemblyQuantity.emit("input");
              const pendingGeneration = generateButton.emit("click");
              await Promise.resolve();

              assemblyQuantity.value = "20";
              await assemblyQuantity.emit("input");
              resolveFetch({
                ok: true,
                json: async () => ({
                  assembly_drawing_no: "ASM-100",
                  items: [{manual_id: 1, quantity_per_set: 2}],
                }),
              });
              await pendingGeneration;

              assert.equal(body.rows.length, 1);
              assert.equal(body.rows[0], initialRow, "stale response must not replace current rows");
              assert.match(status.textContent, /改变|重新生成/);
            })().catch((error) => {
              process.nextTick(() => { throw error; });
            });
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_order_delivery_date_fills_rows_and_new_rows_inherit_it(self):
        script = textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            class Element {
              constructor(value = "") {
                this.value = value;
                this.type = "text";
                this.dataset = {};
                this.listeners = {};
                this.disabled = false;
                this.checked = false;
              }
              addEventListener(type, listener) {
                (this.listeners[type] ||= []).push(listener);
              }
              emit(type) {
                for (const listener of this.listeners[type] || []) listener({target: this});
              }
              matches() { return false; }
            }

            class Select extends Element {
              constructor() {
                super("");
                this.options = [{value: "", dataset: {customer: ""}, hidden: false, disabled: false}];
              }
              get selectedOptions() {
                return this.options.filter((option) => option.value === this.value);
              }
            }

            let clonedRow;
            function makeRow() {
              const drawing = new Select();
              const productName = new Select();
              const customerCell = {textContent: "-"};
              const plannedDate = new Element();
              plannedDate.type = "date";
              plannedDate.name = "planned_ship_at";
              const removeButton = new Element();
              const row = {
                querySelector(selector) {
                  return ({
                    "[data-product-drawing-select]": drawing,
                    "[data-product-name-select]": productName,
                    "[data-customer-name-cell]": customerCell,
                    "[data-remove-row]": removeButton,
                    'input[name="planned_ship_at"]': plannedDate,
                  })[selector] || null;
                },
                querySelectorAll(selector) {
                  if (selector === "input") return [plannedDate];
                  if (selector === "[data-status-checkbox]") return [];
                  return [];
                },
                cloneNode() {
                  clonedRow = makeRow();
                  return clonedRow.row;
                },
              };
              return {row, plannedDate};
            }

            const first = makeRow();
            const rows = [first.row];
            const body = new Element();
            body.querySelector = (selector) => selector === "[data-order-item]" ? rows[0] : null;
            body.querySelectorAll = (selector) => selector === "[data-order-item]" ? rows : [];
            body.appendChild = (row) => rows.push(row);

            const addButton = new Element();
            const deliveryDate = new Element();
            deliveryDate.type = "date";
            const form = new Element();
            form.querySelector = (selector) => ({
              "[data-order-items]": body,
              "[data-add-row]": addButton,
              "[data-order-customer-filter]": null,
              "[data-order-delivery-date]": deliveryDate,
            })[selector] || null;
            const root = {querySelector: (selector) => selector === "[data-order-entry]" ? form : null};

            global.document = {querySelector: () => null};
            vm.runInThisContext(fs.readFileSync("static/order_entry.js", "utf8"));
            globalThis.__orderEntryTestApi.initializeOrderEntry(root);

            deliveryDate.value = "2026-10-20";
            deliveryDate.emit("input");
            assert.equal(first.plannedDate.value, "2026-10-20");

            first.plannedDate.value = "2026-10-25";
            addButton.emit("click");
            assert.equal(first.plannedDate.value, "2026-10-25");
            assert.equal(clonedRow.plannedDate.value, "2026-10-20");
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
