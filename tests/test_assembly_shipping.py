import base64
import multiprocessing
import sqlite3
import struct
import subprocess
import tempfile
import textwrap
import unittest
import zlib
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app
import assembly_shipping


def png_chunk(chunk_type, payload):
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def png_bytes(width=1, height=1):
    rows = b"".join(b"\x00" + (b"\x00\x80\xff" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(rows))
        + png_chunk(b"IEND", b"")
    )


VALID_PNG_BYTES = png_bytes()
VALID_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
    "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx"
    "8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi"
    "6KKK+ZP3E//Z"
)
VALID_GIF_BYTES = base64.b64decode(
    "R0lGODdhAgACAIEAAP8AAAAAAAAAAAAAACwAAAAAAgACAAAIBgABCAQQEAA7"
)
VALID_ANIMATED_GIF_BYTES = base64.b64decode(
    "R0lGODlhAgACAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQA"
    "CgAAACwAAAAAAgACAAAIBgABCAQQEAAh+QQBCgABACwAAAAAAgACAIEA/wAAAAAA"
    "AAAAAAAIBgABCAQQEAA7"
)
SEMANTIC_SHELL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
    + png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    + png_chunk(b"IEND", b"")
)
SEMANTIC_SHELL_JPEG_BYTES = (
    b"\xff\xd8"
    + b"\xff\xc0\x00\x0b\x00\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00"
    + b"\xff\xd9"
)
SEMANTIC_SHELL_GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x00\x00\x00"
    + b",\x00\x00\x00\x00\x01\x00\x01\x00\x00"
    + b"\x02\x02\x44\x01\x00;"
)

# Standards-compliant 2x2 VP8 WebP fixtures.  Both were decoded independently
# before being checked in; tests must not depend on an image codec at runtime.
VALID_STATIC_WEBP_BYTES = base64.b64decode(
    "UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoCAAIAAUAmJaACdLoB+AADsAD+8ut/"
    "/NgVzXPv9//S4P0uD9Lg/9KQAAA="
)
VALID_ANIMATED_WEBP_BYTES = base64.b64decode(
    "UklGRnQAAABXRUJQVlA4WAoAAAACAAAAAQAAAQAAQU5JTQYAAAAAAAAAAABBTk1G"
    "SAAAAAAAAAAAAAEAAAEAAGQAAABWUDggMAAAANABAJ0BKgIAAgABQCYloAJ0ugH4"
    "AAOwAP7y63/82BXNc+/3/9Lg/S4P0uD/0pAAAA=="
)
HEADER_ONLY_WEBP_BYTES = (
    b"RIFF"
    + struct.pack("<I", 22)
    + b"WEBPVP8 "
    + struct.pack("<I", 10)
    + b"\x00\x00\x00\x9d\x01\x2a\x01\x00\x01\x00"
)


def _post_in_subprocess(
    db_path,
    write_lock_path,
    route,
    data,
    start_event,
    result_queue,
    completed_event=None,
    ready_event=None,
):
    app.DB_PATH = Path(db_path)
    app.app.config["WRITE_LOCK_PATH"] = str(write_lock_path)
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_role"] = "admin"
    if ready_event is not None:
        ready_event.set()
    start_event.wait(10)
    try:
        response = client.post(route, data=data)
        result_queue.put((response.status_code, response.get_json(silent=True)))
    except Exception as error:
        result_queue.put(("error", f"{type(error).__name__}: {error}"))
    finally:
        if completed_event is not None:
            completed_event.set()


def _get_in_subprocess(
    db_path,
    write_lock_path,
    route,
    start_event,
    result_queue,
    completed_event=None,
):
    app.DB_PATH = Path(db_path)
    app.app.config["WRITE_LOCK_PATH"] = str(write_lock_path)
    app.DATABASE_READY = True
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_role"] = "admin"
    start_event.wait(10)
    try:
        response = client.get(route)
        result_queue.put((response.status_code, response.get_json(silent=True)))
    except Exception as error:
        result_queue.put(("error", f"{type(error).__name__}: {error}"))
    finally:
        if completed_event is not None:
            completed_event.set()


def _acquire_write_lock_in_subprocess(write_lock_path, result_queue):
    app.app.config["WRITE_LOCK_PATH"] = str(write_lock_path)
    try:
        with app.application_write_lock():
            result_queue.put("acquired")
    except Exception as error:
        result_queue.put(f"{type(error).__name__}: {error}")


class AssemblyShippingDomainTests(unittest.TestCase):
    def test_zero_actual_quantity_preserves_calculation_without_allocations_or_stock(self):
        rows = assembly_shipping.expand_components(
            [{"manual_id": 7, "quantity_per_set": 2}], 10, {7: 0}
        )
        self.assertEqual(rows[0]["calculated_quantity"], 20)
        self.assertEqual(rows[0]["shipped_quantity"], 0)
        self.assertEqual(assembly_shipping.allocate_quantity(0, [{"id": 1, "unshipped_quantity": 9}]), [])
        self.assertEqual(assembly_shipping.inventory_result(0, 0), {
            "requested_quantity": 0, "deducted_quantity": 0, "shortage_quantity": 0,
        })

    def test_actual_quantity_rejects_negative_fraction_boolean_and_overflow(self):
        for quantity in [-1, 1.5, True, "", "01", 2_147_483_648]:
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                assembly_shipping.expand_components(
                    [{"manual_id": 7, "quantity_per_set": 2}], 10, {7: quantity}
                )

    def test_normalize_component_rows_ignores_blank_rows_and_rejects_duplicates(self):
        rows = assembly_shipping.normalize_component_rows(
            [" ASM-100 ", "", "ASM-200"], ["2", "", "3"]
        )
        self.assertEqual(
            rows,
            [
                {
                    "assembly_drawing_no": "ASM-100",
                    "quantity_per_set": 2,
                    "sort_order": 0,
                },
                {
                    "assembly_drawing_no": "ASM-200",
                    "quantity_per_set": 3,
                    "sort_order": 1,
                },
            ],
        )
        with self.assertRaisesRegex(ValueError, "不能重复"):
            assembly_shipping.normalize_component_rows(
                ["ASM-100", "ASM-100"], ["1", "2"]
            )

    def test_expand_components_uses_override_without_losing_calculated_quantity(self):
        result = assembly_shipping.expand_components(
            [
                {
                    "manual_id": 7,
                    "drawing_no": "P1",
                    "product_name": "配件1",
                    "quantity_per_set": 2,
                }
            ],
            100,
            {7: 180},
        )
        self.assertEqual(result[0]["calculated_quantity"], 200)
        self.assertEqual(result[0]["shipped_quantity"], 180)

    def test_allocate_quantity_splits_orders_and_leaves_direct_remainder(self):
        orders = [
            {"id": 11, "unshipped_quantity": 60},
            {"id": 12, "unshipped_quantity": 80},
        ]
        self.assertEqual(
            assembly_shipping.allocate_quantity(200, orders),
            [
                {"order_id": 11, "quantity": 60},
                {"order_id": 12, "quantity": 80},
                {"order_id": None, "quantity": 60},
            ],
        )

    def test_inventory_result_never_produces_negative_stock(self):
        self.assertEqual(
            assembly_shipping.inventory_result(280, 250),
            {
                "requested_quantity": 280,
                "deducted_quantity": 250,
                "shortage_quantity": 30,
            },
        )

    def test_preview_token_is_stable_and_changes_with_order_or_stock_state(self):
        first = {"customer": "客户A", "items": [{"manual_id": 1, "stock": 5}]}
        self.assertEqual(
            assembly_shipping.preview_token(first),
            assembly_shipping.preview_token(first),
        )
        changed = {"customer": "客户A", "items": [{"manual_id": 1, "stock": 4}]}
        self.assertNotEqual(
            assembly_shipping.preview_token(first),
            assembly_shipping.preview_token(changed),
        )

    def test_preview_token_ignores_existing_token_without_mutating_preview(self):
        preview = {"customer": "客户A", "preview_token": "old-token"}
        without_token = {"customer": "客户A"}
        self.assertEqual(
            assembly_shipping.preview_token(preview),
            assembly_shipping.preview_token(without_token),
        )
        self.assertEqual(preview["preview_token"], "old-token")


class AssemblyAppTestCase(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        self.original_write_lock_path = app.app.config.get("WRITE_LOCK_PATH")
        app.app.config["WRITE_LOCK_PATH"] = str(
            Path(self.tmpdir.name) / "http-writes.lock"
        )
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        if self.original_write_lock_path is None:
            app.app.config.pop("WRITE_LOCK_PATH", None)
        else:
            app.app.config["WRITE_LOCK_PATH"] = self.original_write_lock_path
        self.tmpdir.cleanup()

    def create_product(self, drawing_no="P1", customer="客户A"):
        now = "2026-08-30T09:00:00"
        with app.get_db() as conn:
            return conn.execute(
                """
                INSERT INTO manuals (
                    product_name, customer, model, category, version, remark,
                    filename, original_filename, created_at, updated_at,
                    drawing_no, supplier, pack_quantity, pack_carton_size,
                    sku, barcode, min_stock
                ) VALUES (?, ?, '', '', '', '', '', '', ?, ?, ?, '', '', '', '', '', 0)
                """,
                (f"产品-{drawing_no}", customer, now, now, drawing_no),
            ).lastrowid

    def valid_product_form(self, customer="客户A", drawing_no="P1"):
        return {
            "drawing_no": drawing_no,
            "product_name": f"产品-{drawing_no}",
            "supplier": "",
            "customer": customer,
            "pack_quantity": "",
            "pack_carton_size": "",
            "pack_weight": "",
            "sku": "",
            "barcode": "",
            "default_location_id": "",
            "min_stock": "0",
            "remark": "",
            "description_html": "",
        }

    def configure_component(
        self, manual_id, assembly_drawing_no="ASM-100", quantity_per_set=1, sort_order=0
    ):
        with app.get_db() as conn:
            app.save_product_assembly_components(
                conn,
                manual_id,
                [
                    {
                        "assembly_drawing_no": assembly_drawing_no,
                        "quantity_per_set": quantity_per_set,
                        "sort_order": sort_order,
                    }
                ],
                "2026-08-30T10:00:00",
            )

    def create_order(
        self,
        manual_id,
        order_no,
        quantity,
        customer="客户A",
        planned_ship_at="2026-09-01",
        ordered_at="2026-08-01",
        created_at="2026-08-30T09:00:00",
        shipped_quantity=0,
    ):
        with app.get_db() as conn:
            order_id = conn.execute(
                """
                INSERT INTO product_orders (
                    manual_id, order_no, ordered_at, quantity, customer,
                    planned_ship_at, shipped_quantity, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manual_id,
                    order_no,
                    ordered_at,
                    quantity,
                    customer,
                    planned_ship_at,
                    shipped_quantity,
                    created_at,
                    created_at,
                ),
            ).lastrowid
        return order_id

    def create_shipment(self, order_id, quantity, shipped_at="2026-08-30"):
        with app.get_db() as conn:
            return conn.execute(
                """
                INSERT INTO product_order_shipments (
                    order_id, shipped_quantity, shipped_at, created_at
                ) VALUES (?, ?, ?, '2026-08-30T11:00:00')
                """,
                (order_id, quantity, shipped_at),
            ).lastrowid

    def stock_product(self, manual_id, quantity):
        now = "2026-08-30T09:30:00"
        with app.get_db() as conn:
            location = conn.execute(
                "SELECT id FROM warehouse_locations ORDER BY id LIMIT 1"
            ).fetchone()
            if location is None:
                location_id = conn.execute(
                    """
                    INSERT INTO warehouse_locations (
                        name, code, remark, enabled, created_at, updated_at
                    ) VALUES ('预览测试库位', 'PREVIEW', '', 1, ?, ?)
                    """,
                    (now, now),
                ).lastrowid
            else:
                location_id = location["id"]
            conn.execute(
                """
                INSERT INTO inventory_balances (
                    manual_id, location_id, quantity, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(manual_id, location_id)
                DO UPDATE SET quantity = excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (manual_id, location_id, quantity, now),
            )

    def post_preview(self, customer="客户A", assembly="ASM-100", sets=100, overrides=None):
        payload = {
            "customer": customer,
            "assembly_drawing_no": assembly,
            "set_quantity": sets,
        }
        if overrides is not None:
            payload["overrides"] = overrides
        return self.client.post(
            "/admin/shipped-orders/assembly-preview", json=payload
        )


class AssemblyDatabaseTests(AssemblyAppTestCase):
    def test_init_db_creates_assembly_tables_and_indexes_idempotently(self):
        app.init_db()
        app.init_db()

        with app.get_db() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }

        self.assertTrue(
            {
                "product_assembly_components",
                "assembly_shipment_batches",
                "assembly_shipment_items",
                "assembly_shipment_allocations",
                "assembly_shipment_images",
            }.issubset(tables)
        )
        self.assertTrue(
            {
                "idx_product_assembly_components_manual_id",
                "idx_product_assembly_components_drawing_manual",
                "idx_assembly_shipment_batches_shipped_customer",
                "idx_assembly_shipment_items_batch_id",
                "idx_assembly_shipment_allocations_item_id",
                "idx_assembly_shipment_allocations_order_id",
                "idx_assembly_shipment_images_batch_id",
            }.issubset(indexes)
        )

    def test_save_product_assembly_components_replaces_only_one_product_in_display_order(self):
        first_manual_id = self.create_product("P1")
        second_manual_id = self.create_product("P2")
        first_now = "2026-08-30T10:00:00"
        second_now = "2026-08-30T11:00:00"

        with app.get_db() as conn:
            app.save_product_assembly_components(
                conn,
                first_manual_id,
                [
                    {
                        "assembly_drawing_no": "ASM-100",
                        "quantity_per_set": 2,
                        "sort_order": 0,
                    },
                    {
                        "assembly_drawing_no": "ASM-200",
                        "quantity_per_set": 3,
                        "sort_order": 1,
                    },
                ],
                first_now,
            )
            app.save_product_assembly_components(
                conn,
                second_manual_id,
                [
                    {
                        "assembly_drawing_no": "ASM-OTHER",
                        "quantity_per_set": 1,
                        "sort_order": 0,
                    }
                ],
                first_now,
            )
            app.save_product_assembly_components(
                conn,
                first_manual_id,
                [
                    {
                        "assembly_drawing_no": "ASM-200",
                        "quantity_per_set": 4,
                        "sort_order": 8,
                    },
                    {
                        "assembly_drawing_no": "ASM-300",
                        "quantity_per_set": 5,
                        "sort_order": 2,
                    },
                ],
                second_now,
            )
            first_rows = app.get_product_assembly_components(conn, first_manual_id)
            second_rows = app.get_product_assembly_components(conn, second_manual_id)

        self.assertEqual(
            [
                (
                    row["assembly_drawing_no"],
                    row["quantity_per_set"],
                    row["sort_order"],
                    row["created_at"],
                    row["updated_at"],
                )
                for row in first_rows
            ],
            [
                ("ASM-300", 5, 2, second_now, second_now),
                ("ASM-200", 4, 8, second_now, second_now),
            ],
        )
        self.assertEqual(
            [(row["assembly_drawing_no"], row["quantity_per_set"]) for row in second_rows],
            [("ASM-OTHER", 1)],
        )

    def test_delete_product_explicitly_removes_assembly_components_without_foreign_keys(self):
        manual_id = self.create_product("P-DELETE")
        with app.get_db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 0)
            app.save_product_assembly_components(
                conn,
                manual_id,
                [
                    {
                        "assembly_drawing_no": "ASM-DELETE",
                        "quantity_per_set": 2,
                        "sort_order": 0,
                    }
                ],
                "2026-08-30T12:00:00",
            )

        response = self.client.post(f"/admin/{manual_id}/delete")
        self.assertEqual(response.status_code, 302)

        with app.get_db() as conn:
            component_count = conn.execute(
                "SELECT COUNT(*) FROM product_assembly_components WHERE manual_id = ?",
                (manual_id,),
            ).fetchone()[0]
        self.assertEqual(component_count, 0)


class ProductAssemblyEditorTests(AssemblyAppTestCase):
    def test_edit_product_renders_and_saves_multiple_assembly_memberships(self):
        manual_id = self.create_product(customer="客户A")

        html = self.client.get(f"/admin/{manual_id}/edit").get_data(as_text=True)
        self.assertIn("所属组装件", html)
        self.assertIn('name="assembly_drawing_no"', html)
        self.assertIn('name="assembly_quantity_per_set"', html)

        response = self.client.post(
            f"/admin/{manual_id}/edit",
            data={
                **self.valid_product_form(customer="客户A"),
                "assembly_drawing_no": ["ASM-100", "ASM-200"],
                "assembly_quantity_per_set": ["2", "3"],
            },
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            rows = app.get_product_assembly_components(conn, manual_id)
        self.assertEqual(
            [
                (row["assembly_drawing_no"], row["quantity_per_set"])
                for row in rows
            ],
            [("ASM-100", 2), ("ASM-200", 3)],
        )

    def test_edit_product_rejects_assembly_membership_without_customer(self):
        manual_id = self.create_product(customer="")

        response = self.client.post(
            f"/admin/{manual_id}/edit",
            data={
                **self.valid_product_form(customer=""),
                "assembly_drawing_no": ["ASM-100"],
                "assembly_quantity_per_set": ["2"],
            },
            follow_redirects=True,
        )

        self.assertIn("请先选择客户", response.get_data(as_text=True))

    def test_edit_product_keeps_submitted_assembly_rows_after_validation_errors(self):
        manual_id = self.create_product(customer="客户A")
        with app.get_db() as conn:
            app.save_product_assembly_components(
                conn,
                manual_id,
                [
                    {
                        "assembly_drawing_no": "SAVED-ONLY",
                        "quantity_per_set": 1,
                        "sort_order": 0,
                    }
                ],
                "2026-08-30T12:00:00",
            )

        cases = [
            ("客户A", ["ASM-100", "asm-100"], ["2", "3"], "不能重复"),
            ("客户A", ["ASM-200", ""], ["2", "3"], "必须同时填写"),
            ("客户A", ["ASM-300"], ["0"], "必须为大于 0 的整数"),
            ("", ["ASM-400"], ["2"], "请先选择客户"),
        ]
        for customer, drawings, quantities, message in cases:
            with self.subTest(message=message):
                response = self.client.post(
                    f"/admin/{manual_id}/edit",
                    data={
                        **self.valid_product_form(customer=customer),
                        "assembly_drawing_no": drawings,
                        "assembly_quantity_per_set": quantities,
                    },
                    follow_redirects=True,
                )
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn(message, html)
                for drawing in drawings:
                    if drawing:
                        self.assertIn(f'value="{drawing}"', html)
                for quantity in quantities:
                    if quantity:
                        self.assertIn(f'value="{quantity}"', html)
                with app.get_db() as conn:
                    rows = app.get_product_assembly_components(conn, manual_id)
                self.assertEqual(
                    [
                        (row["assembly_drawing_no"], row["quantity_per_set"])
                        for row in rows
                    ],
                    [("SAVED-ONLY", 1)],
                )


class AssemblyPreviewTests(AssemblyAppTestCase):
    def test_ordinary_order_summary_keeps_working_without_assembly_tables(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE product_order_shipments (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    shipped_quantity INTEGER NOT NULL,
                    shipped_at TEXT NOT NULL
                );
                INSERT INTO product_order_shipments (
                    id, order_id, shipped_quantity, shipped_at
                ) VALUES (1, 10, 4, '2026-08-30');
                """
            )

            row = conn.execute(
                f"SELECT * FROM ({app.order_shipment_summary_subquery()})"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual((row["order_id"], row["shipped_total"]), (10, 4))

    def test_options_and_definition_are_customer_scoped_case_insensitive_and_ordered(self):
        p_z = self.create_product("P-Z", "客户A")
        p_a = self.create_product("P-A", "客户A")
        p_m = self.create_product("P-M", "客户A")
        other = self.create_product("OTHER", "客户B")
        self.configure_component(p_z, "ASM-100", 2, sort_order=0)
        self.configure_component(p_a, "asm-100", 3, sort_order=0)
        self.configure_component(p_m, "ASM-100", 4, sort_order=1)
        self.configure_component(other, "ASM-100", 9, sort_order=0)

        response = self.client.get(
            "/admin/shipped-orders/assembly-options", query_string={"customer": " 客户A "}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["assembly_drawing_numbers"], ["ASM-100"])
        with app.get_db() as conn:
            definition = app.get_assembly_definition(conn, "客户A", " AsM-100 ")
        self.assertEqual(
            [(row["drawing_no"], row["quantity_per_set"]) for row in definition],
            [("P-A", 3), ("P-Z", 2), ("P-M", 4)],
        )

    def test_preview_uses_remaining_quantity_fifo_with_blank_dates_last(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id, quantity_per_set=2)
        same_first = self.create_order(
            manual_id, "SO-FIRST", 60, planned_ship_at="2026-09-01", ordered_at="2026-08-01"
        )
        same_second = self.create_order(
            manual_id, "SO-SECOND", 50, planned_ship_at="2026-09-01", ordered_at="2026-08-02"
        )
        blank = self.create_order(
            manual_id, "SO-BLANK", 80, planned_ship_at="", ordered_at="2026-07-01"
        )
        self.create_order(
            manual_id, "SO-OTHER-CUSTOMER", 500, customer="客户B", planned_ship_at="2026-08-01"
        )
        self.create_shipment(same_first, 20)

        response = self.post_preview()

        self.assertEqual(response.status_code, 200)
        item = response.get_json()["items"][0]
        self.assertEqual(item["calculated_quantity"], 200)
        self.assertEqual(
            item["allocations"],
            [
                {"order_id": same_first, "order_no": "SO-FIRST", "quantity": 40},
                {"order_id": same_second, "order_no": "SO-SECOND", "quantity": 50},
                {"order_id": blank, "order_no": "SO-BLANK", "quantity": 80},
                {"order_id": None, "order_no": "", "quantity": 30},
            ],
        )
        self.assertEqual(item["no_order_quantity"], 30)

    def test_override_preserves_calculation_and_emits_structured_warnings_and_stock_split(self):
        manual_id = self.create_product("P2", "客户A")
        self.configure_component(manual_id, quantity_per_set=3)
        self.stock_product(manual_id, 250)

        response = self.post_preview(overrides={str(manual_id): "280"})

        self.assertEqual(response.status_code, 200)
        preview = response.get_json()
        item = preview["items"][0]
        self.assertEqual(item["calculated_quantity"], 300)
        self.assertEqual(item["shipped_quantity"], 280)
        self.assertEqual(item["available_inventory"], 250)
        self.assertEqual(item["inventory_deducted_quantity"], 250)
        self.assertEqual(item["inventory_shortage_quantity"], 30)
        self.assertEqual(item["no_order_quantity"], 280)
        self.assertEqual(
            preview["warnings"],
            [
                {
                    "code": "no_order",
                    "manual_id": manual_id,
                    "drawing_no": "P2",
                    "quantity": 280,
                    "message": "配件 P2：280 个没有可匹配订单，将作为直接发货保存",
                },
                {
                    "code": "inventory_shortage",
                    "manual_id": manual_id,
                    "drawing_no": "P2",
                    "quantity": 30,
                    "message": "配件 P2：库存不足 30 个，确认后库存将扣到 0",
                },
            ],
        )

    def test_preview_is_read_only_and_token_is_stable_but_tracks_server_state(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        self.stock_product(manual_id, 100)
        with app.get_db() as conn:
            before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                )
            }

        first = self.post_preview(sets=10).get_json()
        second = self.post_preview(sets=10).get_json()

        self.assertEqual(first["preview_token"], second["preview_token"])
        self.assertEqual(first["preview_token"], assembly_shipping.preview_token(first))
        self.stock_product(manual_id, 9)
        changed = self.post_preview(sets=10).get_json()
        self.assertNotEqual(first["preview_token"], changed["preview_token"])
        with app.get_db() as conn:
            after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(before, after)

    def test_preview_token_uses_canonical_server_assembly_number(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id, "ASM-100", quantity_per_set=1)

        canonical = self.post_preview(assembly="ASM-100", sets=10).get_json()
        case_variant = self.post_preview(assembly="asm-100", sets=10).get_json()

        self.assertEqual(case_variant["assembly_drawing_no"], "ASM-100")
        self.assertEqual(canonical["preview_token"], case_variant["preview_token"])

    def test_excluded_batch_adds_back_only_its_customer_component_order_and_stock(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        order_id = self.create_order(manual_id, "SO-1", 100)
        self.stock_product(manual_id, 40)
        now = "2026-08-30T12:00:00"
        with app.get_db() as conn:
            own_batch = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户A', 'ASM-100', 60, '2026-08-30', '', 'admin', ?, ?)
                """,
                (now, now),
            ).lastrowid
            own_item = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    created_at, updated_at
                ) VALUES (?, ?, 'P1', '产品-P1', 1, 60, 60, 60, 0, ?, ?)
                """,
                (own_batch, manual_id, now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO assembly_shipment_allocations (item_id, order_id, quantity, created_at)
                VALUES (?, ?, 60, ?)
                """,
                (own_item, order_id, now),
            )
            foreign_batch = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户B', 'ASM-100', 10, '2026-08-30', '', 'admin', ?, ?)
                """,
                (now, now),
            ).lastrowid
            foreign_item = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    created_at, updated_at
                ) VALUES (?, ?, 'P1', '产品-P1', 1, 10, 10, 10, 0, ?, ?)
                """,
                (foreign_batch, manual_id, now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO assembly_shipment_allocations (item_id, order_id, quantity, created_at)
                VALUES (?, ?, 10, ?)
                """,
                (foreign_item, order_id, now),
            )

            normal = app.build_assembly_shipment_preview(
                conn, "客户A", "ASM-100", 60
            )
            excluded_foreign = app.build_assembly_shipment_preview(
                conn, "客户A", "ASM-100", 60, exclude_batch_id=foreign_batch
            )
            excluded_own = app.build_assembly_shipment_preview(
                conn, "客户A", "ASM-100", 60, exclude_batch_id=own_batch
            )

        self.assertEqual(normal, excluded_foreign)
        self.assertEqual(normal["items"][0]["available_inventory"], 40)
        self.assertEqual(normal["items"][0]["no_order_quantity"], 30)
        self.assertEqual(excluded_own["items"][0]["available_inventory"], 100)
        self.assertEqual(excluded_own["items"][0]["no_order_quantity"], 0)

    def test_preview_routes_return_json_400_and_404_for_invalid_requests(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id)
        cases = [
            ("options", self.client.get("/admin/shipped-orders/assembly-options"), 400),
            ("blank customer", self.post_preview(customer=""), 400),
            ("blank assembly", self.post_preview(assembly=""), 400),
            ("invalid sets", self.post_preview(sets="0"), 400),
            ("invalid overrides", self.post_preview(overrides=[]), 400),
            ("unknown override", self.post_preview(overrides={"999999": 1}), 400),
            ("missing definition", self.post_preview(assembly="ASM-MISSING"), 404),
        ]
        for label, response, status in cases:
            with self.subTest(label=label):
                self.assertEqual(response.status_code, status)
                self.assertIn("error", response.get_json())

    def test_options_requires_view_permission_and_preview_requires_manage_permission(self):
        now = "2026-08-30T13:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_view_shipped, can_manage_shipped, created_at, updated_at
                ) VALUES ('viewer-only', 'unused', 'operator', 1, 1, 0, ?, ?)
                """,
                (now, now),
            )
        with self.client.session_transaction() as session:
            session["admin_username"] = "viewer-only"
            session["admin_role"] = "operator"

        options = self.client.get(
            "/admin/shipped-orders/assembly-options", query_string={"customer": "客户A"}
        )
        preview = self.post_preview()

        self.assertEqual(options.status_code, 200)
        self.assertEqual(preview.status_code, 302)


class AssemblyShippingPageTests(AssemblyAppTestCase):
    def test_shipping_operations_page_contains_order_and_assembly_modes(self):
        response = self.client.get("/admin/shipped-orders/create")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn("按订单发货", html)
        self.assertIn("按组装件发货", html)
        self.assertIn('data-shipment-create-form', html)
        self.assertIn('data-assembly-shipment-form', html)
        self.assertIn('data-assembly-customer', html)
        self.assertIn('data-assembly-drawing', html)
        self.assertIn('data-assembly-set-quantity', html)
        self.assertIn('data-assembly-preview', html)
        self.assertIn('data-assembly-warning-summary', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('name="images" accept="image/*" multiple', html)
        self.assertNotIn('name="images" accept="image/*" multiple disabled', html)
        self.assertNotIn("当前选择不会上传或保存", html)
        self.assertIn("assembly_shipping.js", html)

    def test_shipped_orders_context_merges_product_and_order_customers_once(self):
        self.create_product("P-PRODUCT-A", "客户A")
        self.create_product("P-PRODUCT-A-LOWER", "客户a")
        ordered_product = self.create_product("P-ORDER-B", "")
        self.create_order(ordered_product, "SO-CUSTOMER-B", 10, customer="客户B")
        captured = {}

        def capture_template(template_name, **context):
            captured.update(context)
            return template_name

        with patch.object(app, "render_template", side_effect=capture_template):
            response = self.client.get("/admin/shipped-orders")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["customers"], ["客户A", "客户B"])

    def test_assembly_shipment_javascript_keeps_editor_and_safe_reconfirmation_contract(self):
        source = (Path(app.BASE_DIR) / "static" / "assembly_shipping.js").read_text(
            encoding="utf-8"
        )

        for function_name in (
            "loadAssemblyOptions",
            "requestAssemblyPreview",
            "renderAssemblyWarnings",
            "renderAssemblyPreview",
            "submitAssemblyShipment",
        ):
            self.assertIn(f"function {function_name}", source)
        self.assertIn("data-assembly-component-editor", source)
        self.assertIn("replaceChildren", source)
        self.assertIn("document.createElement", source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("window.confirm", source)
        self.assertIn("result.status === 409", source)
        self.assertIn("result.status === 201", source)

    def test_assembly_shipment_browser_state_machine_handles_races_and_confirmation(self):
        """Exercise the real browser script with controllable DOM and fetch promises."""
        script = "(async () => {\n" + textwrap.dedent(
            r'''
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            class Element {
              constructor(value = "") {
                this.value = value;
                this.dataset = {};
                this.children = [];
                this.listeners = {};
                this.className = "";
                this.hidden = false;
                this.disabled = false;
                this.classList = {
                  values: new Set(),
                  add: (...values) => values.forEach((value) => this.classList.values.add(value)),
                  toggle: (value, enabled) => {
                    if (enabled) this.classList.values.add(value);
                    else this.classList.values.delete(value);
                  },
                };
              }
              addEventListener(type, listener) {
                (this.listeners[type] ||= []).push(listener);
              }
              async emit(type, event = {target: this, preventDefault() {}}) {
                for (const listener of this.listeners[type] || []) await listener(event);
              }
              append(...items) { this.children.push(...items); }
              appendChild(item) { this.children.push(item); return item; }
              replaceChildren(...items) { this.children = items; }
              setAttribute(name, value) { this[name] = String(value); }
              removeAttribute(name) { delete this[name]; }
              matches(selector) { return selector === "[data-assembly-item-quantity]" && this.dataset.assemblyItemQuantity !== undefined; }
              querySelectorAll() { return []; }
              querySelector() { return null; }
              cloneNode() { const clone = new Element(this.value); clone.dataset = {...this.dataset}; return clone; }
            }

            function deferred() {
              let resolve;
              const promise = new Promise((next) => { resolve = next; });
              return {promise, resolve};
            }
            function response(status, body) { return {status, ok: status >= 200 && status < 300, json: async () => body}; }
            const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

            global.document = {
              querySelectorAll: () => [],
              createElement: () => new Element(),
            };
            const navigations = [];
            let confirmations = 0;
            const confirmationMessages = [];
            global.window = {
              setTimeout,
              clearTimeout,
              confirm: (message) => { confirmations += 1; confirmationMessages.push(message); return true; },
              location: {origin: "https://factory.test", assign: (value) => navigations.push(value)},
            };
            global.FormData = class {
              constructor() { this.values = new Map(); }
              set(key, value) { this.values.set(key, value); }
            };
            global.AbortController = AbortController;

            const requests = [];
            const planned = [];
            global.fetch = (url, options = {}) => {
              const next = planned.shift();
              requests.push({url, options, next});
              return next.promise || Promise.resolve(next);
            };
            vm.runInThisContext(fs.readFileSync("static/assembly_shipping.js", "utf8"));
            const api = globalThis.__assemblyShippingTestApi;
            assert.ok(api, "script must expose its real initializers to the test harness");

            function formFixture() {
              const customer = new Element();
              const drawing = new Element();
              const sets = new Element();
              const submit = new Element();
              const status = new Element();
              const warnings = new Element();
              const preview = new Element();
              const mapping = new Map([
                ["[data-assembly-customer]", customer],
                ["[data-assembly-drawing]", drawing],
                ["[data-assembly-set-quantity]", sets],
                ["[data-assembly-submit]", submit],
                ["[data-assembly-status]", status],
                ["[data-assembly-warning-summary]", warnings],
                ["[data-assembly-preview]", preview],
              ]);
              const form = new Element();
              form.action = "/admin/shipped-orders/assembly/new";
              form.quantities = [];
              form.querySelector = (selector) => mapping.get(selector) || null;
              form.querySelectorAll = (selector) => selector === "[data-assembly-item-quantity]" ? form.quantities : [];
              return {form, customer, drawing, sets, submit, status, warnings, preview};
            }
            function preview(token, warnings = []) {
              return {
                preview_token: token,
                warnings,
                items: [{manual_id: 1, drawing_no: "P1", product_name: "Part", quantity_per_set: 1,
                  calculated_quantity: 2, shipped_quantity: 2, available_inventory: 10,
                  inventory_shortage_quantity: 0, allocations: []}],
              };
            }

            // A/B option requests resolve out of order; only B may populate the current customer.
            const optionsA = deferred();
            const optionsB = deferred();
            planned.push(optionsA, optionsB);
            const race = formFixture();
            api.initializeAssemblyShipmentForm(race.form);
            race.customer.value = "A";
            const changingA = race.customer.emit("change");
            race.customer.value = "B";
            const changingB = race.customer.emit("change");
            optionsB.resolve(response(200, {assembly_drawing_numbers: ["ASM-B"]}));
            await changingB;
            optionsA.resolve(response(200, {assembly_drawing_numbers: ["ASM-A"]}));
            await changingA;
            assert.deepEqual(race.drawing.children.map((option) => option.value), ["", "ASM-B"]);

            // A stale, hanging preview cannot keep the current preview non-submittable.
            const firstPreview = deferred();
            const latestPreview = deferred();
            const saveAfterRace = deferred();
            planned.push(firstPreview, latestPreview, saveAfterRace);
            race.drawing.value = "ASM-B";
            race.sets.value = "1";
            await race.drawing.emit("change");
            await sleep(320);
            race.sets.value = "2";
            await race.sets.emit("input");
            await sleep(320);
            latestPreview.resolve(response(200, preview("latest")));
            await sleep(0);
            assert.equal(race.submit.disabled, false);
            const submitAfterRace = race.form.emit("submit");
            assert.equal(requests.filter((request) => request.url === race.form.action).length, 1);
            saveAfterRace.resolve(response(500, {error: "保存失败"}));
            await submitAfterRace;
            assert.equal(race.submit.disabled, false);

            // Quantity edits debounce and preserve the edited override after the old row is cleared.
            const overridePreview = deferred();
            planned.push(overridePreview);
            const quantity = new Element("7");
            quantity.dataset.assemblyItemQuantity = "";
            quantity.dataset.manualId = "1";
            race.form.quantities = [quantity];
            await race.form.emit("input", {target: quantity, preventDefault() {}});
            race.form.quantities = [];
            await sleep(320);
            const overrideRequest = requests.at(-1);
            assert.equal(JSON.parse(overrideRequest.options.body).overrides["1"], "7");
            overridePreview.resolve(response(200, preview("override")));
            await sleep(0);

            // Warning confirmation is explicit; a 409 only replaces preview and never auto-submits.
            const stale = preview("stale", [{message: "库存不足 3 个"}]);
            race.form._assemblyPreview = stale;
            const confirmedPreview = preview("current", [{message: "库存不足 4 个"}]);
            planned.push(response(409, {error: "请重新确认", preview: confirmedPreview}));
            await race.form.emit("submit");
            const savesAfter409 = requests.filter((request) => request.url === race.form.action).length;
            assert.equal(savesAfter409, 2);
            assert.equal(confirmations, 1);
            assert.match(confirmationMessages[0], /库存不足 3 个/);
            assert.equal(race.form._assemblyPreview.preview_token, "current");
            await sleep(0);
            assert.equal(requests.filter((request) => request.url === race.form.action).length, savesAfter409);

            // Valid same-origin redirect is followed, but external redirect and fetch failure recover locally.
            planned.push(response(201, {redirect_url: "/admin/shipped-orders"}));
            await race.form.emit("submit");
            assert.equal(navigations.length, 1);
            for (const redirect of ["/admin/delivery-notes/operations/1", "https://factory.test/admin/delivery-notes/operations/42"]) {
              planned.push(response(201, {redirect_url: redirect}));
              const before = navigations.length;
              await race.form.emit("submit");
              assert.equal(navigations.length, before + 1, `201 must navigate to ${redirect}`);
              assert.equal(navigations.at(-1), new URL(redirect, window.location.origin).href);
            }
            const invalid = formFixture();
            api.initializeAssemblyShipmentForm(invalid.form);
            invalid.form._assemblyPreview = preview("invalid");
            for (const redirect of ["https://evil.example/", "https://evil.example/admin/delivery-notes/operations/1",
                "/admin/delivery-notes/operations/0", "/admin/delivery-notes/operations/-1",
                "/admin/delivery-notes/operations/01", "/admin/delivery-notes/operations/1.2",
                "/admin/delivery-notes/operations/nope", "/admin/delivery-notes/operations/1/extra", "/admin/users"]) {
              planned.push(response(201, {redirect_url: redirect}));
              const before = navigations.length;
              await invalid.form.emit("submit");
              assert.equal(navigations.length, before, `must not navigate to ${redirect}`);
              assert.equal(invalid.submit.disabled, false);
              assert.match(invalid.status.textContent, /跳转地址/);
            }
            planned.push(response(500, {error: "保存失败"}));
            await invalid.form.emit("submit");
            assert.equal(invalid.submit.disabled, false);

            // Concurrent submit events create only one save request.
            const delayedSave = deferred();
            planned.push(delayedSave);
            invalid.form._assemblyPreview = preview("double");
            const saveCountBeforeDouble = requests.filter((request) => request.url === invalid.form.action).length;
            const one = invalid.form.emit("submit");
            const two = invalid.form.emit("submit");
            assert.equal(requests.filter((request) => request.url === invalid.form.action).length, saveCountBeforeDouble + 1);
            delayedSave.resolve(response(500, {error: "保存失败"}));
            await Promise.all([one, two]);

            // Edit mode consumes the server initial preview and refreshes through its trusted batch URL.
            const edit = formFixture();
            edit.form.action = "/admin/shipped-orders/assembly/7/edit";
            edit.form.dataset.assemblyPreviewUrl = edit.form.action;
            const initialPreview = new Element();
            initialPreview.textContent = JSON.stringify(preview("edit-initial"));
            const editQuerySelector = edit.form.querySelector;
            edit.form.querySelector = (selector) => selector === "[data-assembly-initial-preview]"
              ? initialPreview
              : editQuerySelector(selector);
            api.initializeAssemblyShipmentForm(edit.form);
            assert.equal(edit.form._assemblyPreview.preview_token, "edit-initial");
            edit.customer.value = "A";
            edit.drawing.value = "ASM-EDIT";
            edit.sets.value = "2";
            const editQuantity = new Element("3");
            editQuantity.dataset.assemblyItemQuantity = "";
            editQuantity.dataset.manualId = "1";
            edit.form.quantities = [editQuantity];
            planned.push(response(200, preview("edit-current")));
            await edit.form.emit("input", {target: editQuantity, preventDefault() {}});
            await sleep(320);
            assert.equal(requests.at(-1).url, edit.form.action);
            assert.equal(JSON.parse(requests.at(-1).options.body).overrides["1"], "3");
            await sleep(0);
            assert.equal(edit.form._assemblyPreview.preview_token, "edit-current");

            // Task 3 editor remains a real, interactive add/remove behavior in the shared file.
            const input = new Element("ASM-1");
            const remove = new Element();
            const row = new Element();
            row.querySelectorAll = () => [input];
            row.querySelector = () => remove;
            const rows = new Element();
            rows.querySelectorAll = () => rows.children;
            rows.children = [row];
            rows.appendChild = (item) => { rows.children.push(item); return item; };
            const template = new Element();
            let cloneRemove;
            template.cloneNode = () => {
              const clone = new Element();
              clone.querySelectorAll = () => [new Element("template")];
              cloneRemove = new Element();
              clone.querySelector = () => cloneRemove;
              clone.remove = () => { rows.children = rows.children.filter((item) => item !== clone); };
              return clone;
            };
            const add = new Element();
            const editor = new Element();
            editor.querySelector = (selector) => ({
              "[data-assembly-component-rows]": rows,
              "[data-empty-assembly-component-row]": template,
              "[data-add-assembly-component-row]": add,
            })[selector];
            const root = {querySelectorAll: () => [editor]};
            api.initializeAssemblyComponentEditors(root);
            await remove.emit("click");
            assert.equal(input.value, "");
            await add.emit("click");
            assert.equal(rows.children.length, 2);
            await cloneRemove.emit("click");
            assert.equal(rows.children.length, 1);
            '''
        ) + "\n})().catch((error) => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run(
            ["node", "-e", script],
            cwd=app.BASE_DIR,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class AssemblySaveTests(AssemblyAppTestCase):
    def save_data(
        self,
        preview,
        *,
        manual_ids=None,
        quantities=None,
        preview_token=None,
        confirm_warnings="1",
        extra_data=None,
    ):
        items = preview["items"]
        data = {
            "customer": preview["customer"],
            "assembly_drawing_no": preview["assembly_drawing_no"],
            "set_quantity": str(preview["set_quantity"]),
            "manual_id": manual_ids
            if manual_ids is not None
            else [str(item["manual_id"]) for item in items],
            "shipped_quantity": quantities
            if quantities is not None
            else [str(item["shipped_quantity"]) for item in items],
            "shipped_at": "2026-08-30",
            "logistics_no": "测试批次",
            "preview_token": preview["preview_token"]
            if preview_token is None
            else preview_token,
            "confirm_warnings": confirm_warnings,
        }
        if extra_data:
            data.update(extra_data)
        return data

    def post_save(
        self,
        preview,
        *,
        manual_ids=None,
        quantities=None,
        preview_token=None,
        confirm_warnings="1",
        extra_data=None,
    ):
        data = self.save_data(
            preview,
            manual_ids=manual_ids,
            quantities=quantities,
            preview_token=preview_token,
            confirm_warnings=confirm_warnings,
            extra_data=extra_data,
        )
        return self.client.post("/admin/shipped-orders/assembly/new", data=data)

    def configure_components(self, components, assembly_drawing_no="ASM-100"):
        now = "2026-08-30T10:00:00"
        with app.get_db() as conn:
            for sort_order, (manual_id, quantity_per_set) in enumerate(components):
                app.save_product_assembly_components(
                    conn,
                    manual_id,
                    [
                        {
                            "assembly_drawing_no": assembly_drawing_no,
                            "quantity_per_set": quantity_per_set,
                            "sort_order": sort_order,
                        }
                    ],
                    now,
                )

    def create_assembly_allocation(
        self,
        manual_id,
        order_id,
        quantity,
        *,
        shipped_at="2026-08-31",
        assembly_drawing_no="ASM-100",
    ):
        now = "2026-08-30T12:00:00"
        with app.get_db() as conn:
            batch_id = conn.execute(
                """
                INSERT INTO assembly_shipment_batches (
                    customer, assembly_drawing_no, set_quantity, shipped_at,
                    logistics_no, created_by, created_at, updated_at
                ) VALUES ('客户A', ?, ?, ?, '', 'admin', ?, ?)
                """,
                (assembly_drawing_no, quantity, shipped_at, now, now),
            ).lastrowid
            item_id = conn.execute(
                """
                INSERT INTO assembly_shipment_items (
                    batch_id, manual_id, drawing_no, product_name,
                    quantity_per_set, calculated_quantity, shipped_quantity,
                    inventory_deducted_quantity, inventory_shortage_quantity,
                    created_at, updated_at
                ) VALUES (?, ?, 'P1', '产品-P1', 1, ?, ?, 0, ?, ?, ?)
                """,
                (batch_id, manual_id, quantity, quantity, quantity, now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO assembly_shipment_allocations (
                    item_id, order_id, quantity, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (item_id, order_id, quantity, now),
            )
            app.sync_order_shipment_summary(conn, order_id, updated_at=now)
        return batch_id

    def test_assembly_shipment_snapshots_each_server_price_and_ignores_browser_prices(self):
        first = self.create_product("P-PRICE-1", "客户A")
        second = self.create_product("P-PRICE-2", "客户A")
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 100, currency = 'CNY' WHERE id = ?",
                (first,),
            )
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 250, currency = 'CNY' WHERE id = ?",
                (second,),
            )
        self.configure_components([(first, 2), (second, 3)])
        self.create_order(first, "SO-PRICE-1", 2)
        self.create_order(second, "SO-PRICE-2", 3)
        self.stock_product(first, 2)
        self.stock_product(second, 3)
        preview = self.post_preview(sets=1).get_json()

        response = self.post_save(
            preview,
            extra_data={
                "unit_price_minor": ["999", "999"],
                "currency": ["USD", "USD"],
                "price_recorded_by": "browser",
            },
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with app.get_db() as conn:
            items = conn.execute(
                """
                SELECT manual_id, shipped_quantity, unit_price_minor, currency,
                       price_recorded_by, price_recorded_at
                FROM assembly_shipment_items
                ORDER BY manual_id
                """
            ).fetchall()
        self.assertEqual(
            [
                (
                    row["manual_id"],
                    row["shipped_quantity"],
                    row["unit_price_minor"],
                    row["currency"],
                    row["price_recorded_by"],
                    bool(row["price_recorded_at"]),
                )
                for row in items
            ],
            [
                (first, 2, 100, "CNY", "admin", True),
                (second, 3, 250, "CNY", "admin", True),
            ],
        )

    def test_confirmed_shipment_saves_server_preview_with_mixed_allocation_and_shortage(self):
        manual_id = self.create_product("P1", "客户A")
        self.configure_component(manual_id, quantity_per_set=2)
        order_id = self.create_order(manual_id, "SO-1", 140)
        self.stock_product(manual_id, 170)
        preview = self.post_preview().get_json()

        response = self.post_save(
            preview,
            extra_data={
                "drawing_no": "CLIENT-TAMPERED",
                "calculated_quantity": "999999",
                "inventory_deducted_quantity": "999999",
                "allocation_order_id": "999999",
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertGreater(body["batch_id"], 0)
        self.assertRegex(body["redirect_url"], r"^/admin/delivery-notes/operations/\d+$")
        self.assertEqual(self.client.get(body["redirect_url"]).status_code, 200)
        with app.get_db() as conn:
            batch = conn.execute("SELECT * FROM assembly_shipment_batches").fetchone()
            item = conn.execute("SELECT * FROM assembly_shipment_items").fetchone()
            allocations = conn.execute(
                "SELECT order_id, quantity FROM assembly_shipment_allocations ORDER BY id"
            ).fetchall()
            transactions = conn.execute(
                "SELECT * FROM inventory_transactions WHERE related_order_type = '组装发货' ORDER BY id"
            ).fetchall()
            order = conn.execute(
                "SELECT shipped_quantity, shipped_at FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            open_order_ids = {
                row["id"] for row in app.get_unshipped_order_options(conn)
            }

        self.assertEqual(
            (
                batch["customer"],
                batch["assembly_drawing_no"],
                batch["set_quantity"],
                batch["shipped_at"],
                batch["logistics_no"],
                batch["created_by"],
            ),
            ("客户A", "ASM-100", 100, "2026-08-30", "测试批次", "admin"),
        )
        self.assertEqual(
            (
                item["manual_id"],
                item["drawing_no"],
                item["product_name"],
                item["quantity_per_set"],
                item["calculated_quantity"],
                item["shipped_quantity"],
                item["inventory_deducted_quantity"],
                item["inventory_shortage_quantity"],
            ),
            (manual_id, "P1", "产品-P1", 2, 200, 200, 170, 30),
        )
        self.assertEqual(
            [(row["order_id"], row["quantity"]) for row in allocations],
            [(order_id, 140), (None, 60)],
        )
        self.assertEqual(sum(row["quantity"] for row in transactions), 170)
        self.assertTrue(
            all(
                row["related_order_id"]
                == app.assembly_inventory_related_id(item["id"])
                for row in transactions
            )
        )
        self.assertEqual((order["shipped_quantity"], order["shipped_at"]), (140, "2026-08-30"))
        self.assertNotIn(order_id, open_order_ids)

    def test_assembly_inventory_deduction_prefers_default_location_and_reverses_exactly(self):
        manual_id = self.create_product("P-STOCK", "客户A")
        now = "2026-08-30T09:30:00"
        with app.get_db() as conn:
            other_location_id = conn.execute(
                """
                INSERT INTO warehouse_locations (name, code, remark, enabled, created_at, updated_at)
                VALUES ('其他库位', 'OTHER', '', 1, ?, ?)
                """,
                (now, now),
            ).lastrowid
            default_location_id = conn.execute(
                """
                INSERT INTO warehouse_locations (name, code, remark, enabled, created_at, updated_at)
                VALUES ('默认库位', 'PRIMARY', '', 1, ?, ?)
                """,
                (now, now),
            ).lastrowid
            conn.execute(
                "UPDATE manuals SET default_location_id = ? WHERE id = ?",
                (default_location_id, manual_id),
            )
            conn.executemany(
                """
                INSERT INTO inventory_balances (manual_id, location_id, quantity, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (manual_id, other_location_id, 50, now),
                    (manual_id, default_location_id, 70, now),
                ],
            )

            with app.app.test_request_context():
                deducted = app.deduct_inventory_allow_shortage(
                    conn,
                    item_id=55,
                    manual_id=manual_id,
                    quantity=150,
                    customer="客户A",
                    reference="ASM-100",
                )
            rows = conn.execute(
                """
                SELECT * FROM inventory_transactions
                WHERE related_order_type = '组装发货'
                ORDER BY id
                """
            ).fetchall()
            zero_balances = conn.execute(
                "SELECT quantity FROM inventory_balances WHERE manual_id = ? ORDER BY location_id",
                (manual_id,),
            ).fetchall()
            restored = app.reverse_assembly_inventory_deduction(conn, 55)
            remaining_transactions = conn.execute(
                """
                SELECT COUNT(*)
                FROM inventory_transactions
                WHERE related_order_type = '组装发货'
                  AND related_order_id = 'assembly-item:55'
                """
            ).fetchone()[0]
            restored_again = app.reverse_assembly_inventory_deduction(conn, 55)
            restored_balances = conn.execute(
                "SELECT location_id, quantity FROM inventory_balances WHERE manual_id = ? ORDER BY location_id",
                (manual_id,),
            ).fetchall()

        self.assertEqual(deducted, 120)
        self.assertEqual(
            [(row["from_location_id"], row["quantity"]) for row in rows],
            [(default_location_id, 70), (other_location_id, 50)],
        )
        self.assertTrue(all(row["related_order_id"] == "assembly-item:55" for row in rows))
        self.assertTrue(all(row["related_order_no"] == "ASM-100" for row in rows))
        self.assertTrue(all(row["customer"] == "客户A" for row in rows))
        self.assertEqual([row["quantity"] for row in zero_balances], [0, 0])
        self.assertEqual(restored, 120)
        self.assertEqual(remaining_transactions, 0)
        self.assertEqual(restored_again, 0)
        self.assertEqual(
            [(row["location_id"], row["quantity"]) for row in restored_balances],
            [(other_location_id, 50), (default_location_id, 70)],
        )

    def test_warnings_require_exact_confirmation_and_save_nothing(self):
        manual_id = self.create_product("P-WARN", "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        self.stock_product(manual_id, 3)
        preview = self.post_preview(sets=10).get_json()

        response = self.post_save(preview, confirm_warnings="yes")

        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body["preview"], preview)
        self.assertIn("确认", body["error"])
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(app.inventory_total_for_manual(conn, manual_id), 3)

    def test_stock_change_after_preview_returns_current_preview_and_saves_nothing(self):
        manual_id = self.create_product("P-STOCK-STALE", "客户A")
        self.configure_component(manual_id, quantity_per_set=2)
        self.create_order(manual_id, "SO-STOCK", 200)
        self.stock_product(manual_id, 200)
        preview = self.post_preview().get_json()
        self.stock_product(manual_id, 170)

        response = self.post_save(preview)

        self.assertEqual(response.status_code, 409)
        current = response.get_json()["preview"]
        self.assertNotEqual(current["preview_token"], preview["preview_token"])
        self.assertEqual(current["items"][0]["inventory_shortage_quantity"], 30)
        self.assertEqual(
            [warning["code"] for warning in current["warnings"]],
            ["inventory_shortage"],
        )
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(app.inventory_total_for_manual(conn, manual_id), 170)

    def test_order_change_after_preview_returns_reallocated_current_preview(self):
        manual_id = self.create_product("P-ORDER-STALE", "客户A")
        self.configure_component(manual_id, quantity_per_set=2)
        order_id = self.create_order(manual_id, "SO-ORDER", 200)
        self.stock_product(manual_id, 200)
        preview = self.post_preview().get_json()
        self.create_shipment(order_id, 50)

        response = self.post_save(preview)

        self.assertEqual(response.status_code, 409)
        current = response.get_json()["preview"]
        self.assertEqual(
            current["items"][0]["allocations"],
            [
                {"order_id": order_id, "order_no": "SO-ORDER", "quantity": 150},
                {"order_id": None, "order_no": "", "quantity": 50},
            ],
        )
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )

    def test_structural_item_lists_must_match_definition_exactly_once(self):
        first = self.create_product("P-FIRST", "客户A")
        second = self.create_product("P-SECOND", "客户A")
        self.configure_components([(first, 1), (second, 2)])
        preview = self.post_preview(sets=10).get_json()
        ids = [str(item["manual_id"]) for item in preview["items"]]
        quantities = [str(item["shipped_quantity"]) for item in preview["items"]]
        unknown_id = str(max(first, second) + 9999)
        cases = [
            ("duplicate", [ids[0], ids[0]], quantities),
            ("omitted", [ids[0]], [quantities[0]]),
            ("unknown", [ids[0], unknown_id], quantities),
            ("missing quantity", ids, [quantities[0]]),
            ("extra quantity", [ids[0]], quantities),
            ("negative quantity", ids, ["-1", quantities[1]]),
        ]

        for label, manual_ids, submitted_quantities in cases:
            with self.subTest(label=label):
                response = self.post_save(
                    preview,
                    manual_ids=manual_ids,
                    quantities=submitted_quantities,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                with app.get_db() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_batches"
                        ).fetchone()[0],
                        0,
                    )

    def test_missing_date_or_token_is_structural_error_but_wrong_token_is_stale(self):
        manual_id = self.create_product("P-STRUCTURE", "客户A")
        self.configure_component(manual_id)
        preview = self.post_preview(sets=1).get_json()

        missing_date = self.post_save(preview, extra_data={"shipped_at": ""})
        missing_token = self.post_save(preview, preview_token="")
        wrong_token = self.post_save(preview, preview_token="wrong-token")

        self.assertEqual(missing_date.status_code, 400)
        self.assertEqual(missing_token.status_code, 400)
        self.assertEqual(wrong_token.status_code, 409)
        self.assertEqual(
            wrong_token.get_json()["preview"]["preview_token"],
            preview["preview_token"],
        )

    def test_mid_save_failure_rolls_back_batch_allocations_inventory_and_order_summaries(self):
        first = self.create_product("P-ROLLBACK-1", "客户A")
        second = self.create_product("P-ROLLBACK-2", "客户A")
        self.configure_components([(first, 1), (second, 1)])
        first_order = self.create_order(first, "SO-ROLLBACK-1", 10)
        second_order = self.create_order(second, "SO-ROLLBACK-2", 10)
        self.stock_product(first, 10)
        self.stock_product(second, 10)
        preview = self.post_preview(sets=10).get_json()
        original_insert = app._insert_assembly_shipment_allocation
        call_count = 0

        def fail_on_second_allocation(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise sqlite3.DatabaseError("injected allocation failure")
            return original_insert(*args, **kwargs)

        with patch.object(
            app,
            "_insert_assembly_shipment_allocation",
            side_effect=fail_on_second_allocation,
        ):
            response = self.post_save(preview)

        self.assertEqual(response.status_code, 500)
        with app.get_db() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                )
            }
            assembly_transactions = conn.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE related_order_type = '组装发货'"
            ).fetchone()[0]
            balances = {
                manual_id: app.inventory_total_for_manual(conn, manual_id)
                for manual_id in (first, second)
            }
            orders = conn.execute(
                "SELECT id, shipped_quantity, shipped_at FROM product_orders ORDER BY id"
            ).fetchall()

        self.assertEqual(counts, {table: 0 for table in counts})
        self.assertEqual(assembly_transactions, 0)
        self.assertEqual(balances, {first: 10, second: 10})
        self.assertEqual(
            [(row["id"], row["shipped_quantity"], row["shipped_at"]) for row in orders],
            [(first_order, 0, ""), (second_order, 0, "")],
        )

    def test_save_route_requires_shipped_manage_permission(self):
        manual_id = self.create_product("P-PERMISSION", "客户A")
        self.configure_component(manual_id)
        preview = self.post_preview(sets=1).get_json()
        now = "2026-08-30T13:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_view_shipped, can_manage_shipped, created_at, updated_at
                ) VALUES ('viewer-save', 'unused', 'operator', 1, 1, 0, ?, ?)
                """,
                (now, now),
            )
        with self.client.session_transaction() as session:
            session["admin_username"] = "viewer-save"
            session["admin_role"] = "operator"

        response = self.post_save(preview)

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )

    def test_write_lock_is_local_reentrant_exception_safe_and_cross_process(self):
        self.assertEqual(Path(app.DEFAULT_WRITE_LOCK_PATH).parent, Path("/tmp"))
        self.assertEqual(
            Path(app.app.config["WRITE_LOCK_PATH"]).parent,
            Path(self.tmpdir.name),
        )

        with self.assertRaisesRegex(RuntimeError, "injected"):
            with app.application_write_lock():
                with app.application_write_lock():
                    raise RuntimeError("injected")

        with app.application_write_lock():
            response = self.client.get("/admin/products")
        self.assertEqual(response.status_code, 200)

        context = multiprocessing.get_context("fork")
        result_queue = context.Queue()
        process = context.Process(
            target=_acquire_write_lock_in_subprocess,
            args=(app.app.config["WRITE_LOCK_PATH"], result_queue),
        )
        process.start()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)

        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result_queue.get(timeout=2), "acquired")

    def test_two_workers_with_same_token_serialize_then_return_201_and_409(self):
        self._assert_concurrent_save_replays_once(operation_token=None)

    def test_two_workers_with_same_operation_token_return_identical_201_receipt(self):
        self._assert_concurrent_save_replays_once(operation_token="process-replay")

    def _assert_concurrent_save_replays_once(self, operation_token):
        manual_id = self.create_product("P-CONCURRENT", "客户A")
        self.configure_component(manual_id)
        order_id = self.create_order(manual_id, "SO-CONCURRENT", 100)
        self.stock_product(manual_id, 100)
        preview = self.post_preview().get_json()
        save_data = self.save_data(preview)
        if operation_token is not None:
            save_data["operation_token"] = operation_token
        context = multiprocessing.get_context("fork")
        start_event = context.Event()
        result_queue = context.Queue()
        ready_events = [context.Event(), context.Event()]
        processes = []
        try:
            for ready_event in ready_events:
                process = context.Process(
                    target=_post_in_subprocess,
                    args=(
                        str(app.DB_PATH),
                        app.app.config["WRITE_LOCK_PATH"],
                        "/admin/shipped-orders/assembly/new",
                        save_data,
                        start_event,
                        result_queue,
                        None,
                        ready_event,
                    ),
                )
                process.start()
                processes.append(process)
            # Synchronize before the HTTP write lock. A barrier inside save
            # cannot be reached by the second serialized writer.
            self.assertTrue(all(event.wait(5) for event in ready_events))
            start_event.set()
            # Drain before join: a full 409 preview can exceed the OS pipe
            # buffer, and multiprocessing waits for its feeder on child exit.
            results = [result_queue.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(5)
            self.assertTrue(all(process.exitcode == 0 for process in processes),
                            [(process.pid, process.exitcode) for process in processes])
        finally:
            start_event.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            result_queue.close()
            result_queue.join_thread()

        if operation_token is None:
            self.assertEqual(sorted(result[0] for result in results), [201, 409])
            conflict = next(body for status, body in results if status == 409)
            self.assertIn("preview", conflict)
            self.assertNotEqual(conflict["preview"]["preview_token"], preview["preview_token"])
        else:
            self.assertEqual([result[0] for result in results], [201, 201])
            self.assertEqual(results[0][1], results[1][1])
        with app.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delivery_operations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delivery_notes").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_allocations").fetchone()[0],
                1,
            )
            self.assertEqual(app.inventory_total_for_manual(conn, manual_id), 0)
            order = conn.execute(
                "SELECT shipped_quantity FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(order["shipped_quantity"], 100)

    def test_ordinary_http_write_cannot_interleave_assembly_repreview_and_persist(self):
        manual_id = self.create_product("P-CROSS-WRITER", "客户A")
        self.configure_component(manual_id)
        order_id = self.create_order(manual_id, "SO-CROSS-WRITER", 100)
        self.stock_product(manual_id, 100)
        preview = self.post_preview().get_json()
        context = multiprocessing.get_context("fork")
        assembly_start = context.Event()
        ordinary_start = context.Event()
        preview_ready = context.Event()
        resume_assembly = context.Event()
        ordinary_completed = context.Event()
        assembly_queue = context.Queue()
        ordinary_queue = context.Queue()
        original_preview = app.build_assembly_shipment_preview

        def paused_preview(*args, **kwargs):
            result = original_preview(*args, **kwargs)
            preview_ready.set()
            resume_assembly.wait(5)
            return result

        with patch.object(
            app, "build_assembly_shipment_preview", side_effect=paused_preview
        ):
            assembly_process = context.Process(
                target=_post_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/shipped-orders/assembly/new",
                    self.save_data(preview),
                    assembly_start,
                    assembly_queue,
                ),
            )
            assembly_process.start()
            assembly_start.set()
            self.assertTrue(preview_ready.wait(5))

            ordinary_process = context.Process(
                target=_post_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/shipped-orders/new",
                    {
                        "shipped_at": "2026-08-30",
                        "order_id": [str(order_id)],
                        "shipped_quantity": ["100"],
                    },
                    ordinary_start,
                    ordinary_queue,
                    ordinary_completed,
                ),
            )
            ordinary_process.start()
            ordinary_start.set()
            ordinary_completed.wait(0.5)
            resume_assembly.set()
            for process in (assembly_process, ordinary_process):
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertEqual(assembly_queue.get(timeout=2)[0], 201)
        self.assertEqual(ordinary_queue.get(timeout=2)[0], 302)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM product_order_shipments").fetchone()[0],
                0,
            )
            self.assertEqual(app.inventory_total_for_manual(conn, manual_id), 0)
            order = conn.execute(
                "SELECT shipped_quantity FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(order["shipped_quantity"], 100)

    def test_lazy_write_get_waits_for_failed_assembly_transaction_and_no_state_leaks(self):
        manual_id = self.create_product("P-GET-WRITER", "客户A")
        self.configure_component(manual_id)
        order_id = self.create_order(manual_id, "SO-GET-WRITER", 10)
        self.stock_product(manual_id, 10)
        preview = self.post_preview(sets=10).get_json()
        context = multiprocessing.get_context("fork")
        assembly_start = context.Event()
        get_start = context.Event()
        summary_written = context.Event()
        resume_assembly = context.Event()
        get_completed = context.Event()
        assembly_queue = context.Queue()
        get_queue = context.Queue()
        original_sync = app.sync_order_shipment_summary

        def pause_after_summary_then_fail(conn, synced_order_id, updated_at=""):
            result = original_sync(
                conn, synced_order_id, updated_at=updated_at
            )
            summary_written.set()
            resume_assembly.wait(5)
            raise sqlite3.DatabaseError("injected after summary update")

        app.DATABASE_READY = True
        with patch.object(
            app,
            "sync_order_shipment_summary",
            side_effect=pause_after_summary_then_fail,
        ):
            assembly_process = context.Process(
                target=_post_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/shipped-orders/assembly/new",
                    self.save_data(preview),
                    assembly_start,
                    assembly_queue,
                ),
            )
            assembly_process.start()
            assembly_start.set()
            self.assertTrue(summary_written.wait(5))

            get_process = context.Process(
                target=_get_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/products",
                    get_start,
                    get_queue,
                    get_completed,
                ),
            )
            get_process.start()
            get_start.set()
            get_finished_while_transaction_open = get_completed.wait(0.75)
            resume_assembly.set()
            for process in (assembly_process, get_process):
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertEqual(assembly_queue.get(timeout=2)[0], 500)
        self.assertEqual(get_queue.get(timeout=2)[0], 200)
        with app.get_db() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                )
            }
            assembly_transactions = conn.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE related_order_type = '组装发货'"
            ).fetchone()[0]
            balance = app.inventory_total_for_manual(conn, manual_id)
            order = conn.execute(
                "SELECT shipped_quantity, shipped_at FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            qr_code = conn.execute(
                "SELECT qr_code FROM manuals WHERE id = ?", (manual_id,)
            ).fetchone()["qr_code"]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

        self.assertEqual(counts, {table: 0 for table in counts})
        self.assertEqual(assembly_transactions, 0)
        self.assertEqual(balance, 10)
        self.assertEqual((order["shipped_quantity"], order["shipped_at"]), (0, ""))
        self.assertEqual(qr_code, app.generated_inventory_code(manual_id))
        self.assertEqual(integrity, "ok")
        self.assertFalse(get_finished_while_transaction_open)

    def test_lazy_write_get_cannot_corrupt_assembly_commit(self):
        manual_id = self.create_product("P-GET-COMMIT", "客户A")
        self.configure_component(manual_id)
        order_id = self.create_order(manual_id, "SO-GET-COMMIT", 10)
        self.stock_product(manual_id, 10)
        preview = self.post_preview(sets=10).get_json()
        context = multiprocessing.get_context("fork")
        assembly_start = context.Event()
        get_start = context.Event()
        summary_written = context.Event()
        resume_assembly = context.Event()
        get_completed = context.Event()
        assembly_queue = context.Queue()
        get_queue = context.Queue()
        original_sync = app.sync_order_shipment_summary

        def pause_after_summary(conn, synced_order_id, updated_at=""):
            result = original_sync(
                conn, synced_order_id, updated_at=updated_at
            )
            summary_written.set()
            resume_assembly.wait(5)
            return result

        app.DATABASE_READY = True
        with patch.object(
            app, "sync_order_shipment_summary", side_effect=pause_after_summary
        ):
            assembly_process = context.Process(
                target=_post_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/shipped-orders/assembly/new",
                    self.save_data(preview),
                    assembly_start,
                    assembly_queue,
                ),
            )
            assembly_process.start()
            assembly_start.set()
            self.assertTrue(summary_written.wait(5))

            get_process = context.Process(
                target=_get_in_subprocess,
                args=(
                    str(app.DB_PATH),
                    app.app.config["WRITE_LOCK_PATH"],
                    "/admin/products",
                    get_start,
                    get_queue,
                    get_completed,
                ),
            )
            get_process.start()
            get_start.set()
            get_finished_while_transaction_open = get_completed.wait(0.75)
            resume_assembly.set()
            for process in (assembly_process, get_process):
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        assembly_result = assembly_queue.get(timeout=2)
        get_result = get_queue.get(timeout=2)
        with app.get_db() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                )
            }
            assembly_transactions = conn.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE related_order_type = '组装发货'"
            ).fetchone()[0]
            balance = app.inventory_total_for_manual(conn, manual_id)
            order = conn.execute(
                "SELECT shipped_quantity, shipped_at FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            qr_code = conn.execute(
                "SELECT qr_code FROM manuals WHERE id = ?", (manual_id,)
            ).fetchone()["qr_code"]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

        self.assertEqual(get_result[0], 200)
        self.assertEqual(
            (
                assembly_result[0],
                counts,
                assembly_transactions,
                balance,
                order["shipped_quantity"],
                order["shipped_at"],
            ),
            (
                201,
                {
                    "assembly_shipment_batches": 1,
                    "assembly_shipment_items": 1,
                    "assembly_shipment_allocations": 1,
                },
                1,
                0,
                10,
                "2026-08-30",
            ),
        )
        self.assertEqual(qr_code, app.generated_inventory_code(manual_id))
        self.assertEqual(integrity, "ok")
        self.assertFalse(get_finished_while_transaction_open)

    def test_mixed_shipments_are_consistent_in_order_dashboard_and_edit_reads(self):
        manual_id = self.create_product("P1", "客户A")
        order_id = self.create_order(manual_id, "SO-MIXED-READ", 100)
        self.create_shipment(order_id, 20, shipped_at="2026-08-30")
        self.create_assembly_allocation(
            manual_id, order_id, 30, shipped_at="2026-08-31"
        )

        orders_html = self.client.get("/admin/orders").get_data(as_text=True)
        dashboard_html = self.client.get("/dashboard").get_data(as_text=True)
        captured = {}

        def capture_order_template(template_name, **context):
            captured.update(context)
            return template_name

        with patch.object(app, "render_template", side_effect=capture_order_template):
            edit_response = self.client.get(f"/admin/orders/{order_id}/edit")

        self.assertEqual(edit_response.status_code, 200)
        self.assertIn('data-label="已发货数量">50</td>', orders_html)
        self.assertIn('data-label="未发数量">50</td>', orders_html)
        self.assertIn('data-label="发货时间">2026-08-31</td>', orders_html)
        self.assertRegex(
            dashboard_html, r"未发数量</span>\s*<strong>50</strong>"
        )
        self.assertRegex(
            dashboard_html, r"已发数量</span>\s*<strong>50</strong>"
        )
        self.assertEqual(captured["order"]["shipped_quantity_total"], 50)
        self.assertEqual(captured["order"]["shipped_at_display"], "2026-08-31")

    def test_ordinary_shipment_edit_counts_assembly_and_excludes_itself(self):
        manual_id = self.create_product("P-ORDINARY-EDIT", "客户A")
        order_id = self.create_order(manual_id, "SO-ORDINARY-EDIT", 100)
        shipment_id = self.create_shipment(order_id, 20)
        self.create_assembly_allocation(manual_id, order_id, 30)
        self.stock_product(manual_id, 100)

        response = self.client.post(
            f"/admin/shipped-orders/{shipment_id}/edit",
            data={
                "shipped_at": "2026-08-30",
                "shipped_quantity": "80",
                "logistics_no": "SHOULD-NOT-SAVE",
            },
            follow_redirects=True,
        )

        self.assertIn("修改后的发货数量超过了订单总数量", response.get_data(as_text=True))
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT shipped_quantity FROM product_order_shipments WHERE id = ?",
                (shipment_id,),
            ).fetchone()
            summary = conn.execute(
                f"""
                SELECT shipped_total
                FROM ({app.order_shipment_summary_subquery(include_assembly=True, conn=conn)})
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
        self.assertEqual(shipment["shipped_quantity"], 20)
        self.assertEqual(summary["shipped_total"], 50)

    def order_edit_data(self, manual_id, *, quantity, order_no="SO-LIFECYCLE"):
        return {
            "manual_id": str(manual_id),
            "order_no": order_no,
            "ordered_at": "2026-08-01",
            "quantity": str(quantity),
            "customer_email": "updated@example.com",
            "planned_ship_at": "2026-09-15",
            "material_stock_status": "1",
            "remark": "允许更新的备注",
            "carton_status": "1",
            "recent_ship_status": "1",
        }

    def test_order_edit_blocks_product_change_after_ordinary_or_assembly_shipment(self):
        replacement_manual = self.create_product("P-REPLACEMENT", "客户B")
        cases = []

        ordinary_manual = self.create_product("P-ORDINARY-LIFECYCLE", "客户A")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-ORDINARY-LIFECYCLE", 100
        )
        self.create_shipment(ordinary_order, 10)
        cases.append(("ordinary", ordinary_order, ordinary_manual))

        assembly_manual = self.create_product("P-ASSEMBLY-LIFECYCLE", "客户A")
        assembly_order = self.create_order(
            assembly_manual, "SO-ASSEMBLY-LIFECYCLE", 100
        )
        self.create_assembly_allocation(assembly_manual, assembly_order, 10)
        cases.append(("assembly", assembly_order, assembly_manual))

        for label, order_id, original_manual in cases:
            with self.subTest(reference=label):
                response = self.client.post(
                    f"/admin/orders/{order_id}/edit",
                    data=self.order_edit_data(replacement_manual, quantity=100),
                    follow_redirects=True,
                )

                self.assertIn(
                    "已有发货记录的订单不能更换产品",
                    response.get_data(as_text=True),
                )
                with app.get_db() as conn:
                    order = conn.execute(
                        "SELECT manual_id, customer, quantity FROM product_orders WHERE id = ?",
                        (order_id,),
                    ).fetchone()
                self.assertEqual(
                    (order["manual_id"], order["customer"], order["quantity"]),
                    (original_manual, "客户A", 100),
                )

    def test_order_edit_uses_combined_shipped_total_and_allows_safe_increase(self):
        manual_id = self.create_product("P-MIXED-LIFECYCLE", "客户A")
        order_id = self.create_order(manual_id, "SO-LIFECYCLE", 100)
        self.create_shipment(order_id, 20)
        self.create_assembly_allocation(manual_id, order_id, 30)

        blocked = self.client.post(
            f"/admin/orders/{order_id}/edit",
            data=self.order_edit_data(manual_id, quantity=49),
            follow_redirects=True,
        )
        self.assertIn(
            "订单数量不能小于累计已发数量 50",
            blocked.get_data(as_text=True),
        )

        allowed = self.client.post(
            f"/admin/orders/{order_id}/edit",
            data=self.order_edit_data(
                manual_id, quantity=120, order_no="SO-LIFECYCLE-UPDATED"
            ),
        )
        self.assertEqual(allowed.status_code, 302)
        with app.get_db() as conn:
            order = conn.execute(
                """
                SELECT manual_id, customer, quantity, order_no, planned_ship_at, remark
                FROM product_orders
                WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
        self.assertEqual(
            (
                order["manual_id"],
                order["customer"],
                order["quantity"],
                order["order_no"],
                order["planned_ship_at"],
                order["remark"],
            ),
            (
                manual_id,
                "客户A",
                120,
                "SO-LIFECYCLE-UPDATED",
                "2026-09-15",
                "允许更新的备注",
            ),
        )

    def test_order_edit_does_not_rebind_shipped_order_when_product_customer_changed(self):
        manual_id = self.create_product("P-CUSTOMER-LIFECYCLE", "客户A")
        order_id = self.create_order(
            manual_id, "SO-CUSTOMER-LIFECYCLE", 100, customer="客户A"
        )
        self.create_shipment(order_id, 10)
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET customer = '客户B' WHERE id = ?", (manual_id,)
            )

        response = self.client.post(
            f"/admin/orders/{order_id}/edit",
            data=self.order_edit_data(manual_id, quantity=100),
            follow_redirects=True,
        )

        self.assertIn(
            "已有发货记录的订单不能更换产品或客户",
            response.get_data(as_text=True),
        )
        with app.get_db() as conn:
            order = conn.execute(
                "SELECT manual_id, customer FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual((order["manual_id"], order["customer"]), (manual_id, "客户A"))

    def test_order_delete_blocks_ordinary_and_assembly_references_without_orphans(self):
        cases = []

        ordinary_manual = self.create_product("P-DELETE-ORDINARY", "客户A")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-DELETE-ORDINARY", 100
        )
        ordinary_shipment = self.create_shipment(ordinary_order, 10)
        cases.append(("ordinary", ordinary_order, ordinary_shipment, None))

        assembly_manual = self.create_product("P-DELETE-ASSEMBLY", "客户A")
        assembly_order = self.create_order(
            assembly_manual, "SO-DELETE-ASSEMBLY", 100
        )
        self.create_assembly_allocation(assembly_manual, assembly_order, 10)
        with app.get_db() as conn:
            allocation_id = conn.execute(
                "SELECT id FROM assembly_shipment_allocations WHERE order_id = ?",
                (assembly_order,),
            ).fetchone()["id"]
        cases.append(("assembly", assembly_order, None, allocation_id))

        for label, order_id, shipment_id, allocation_id in cases:
            with self.subTest(reference=label):
                response = self.client.post(
                    f"/admin/orders/{order_id}/delete", follow_redirects=True
                )
                self.assertIn(
                    "已有发货记录的订单不能删除",
                    response.get_data(as_text=True),
                )
                with app.get_db() as conn:
                    self.assertIsNotNone(
                        conn.execute(
                            "SELECT id FROM product_orders WHERE id = ?", (order_id,)
                        ).fetchone()
                    )
                    if shipment_id is not None:
                        self.assertIsNotNone(
                            conn.execute(
                                "SELECT id FROM product_order_shipments WHERE id = ?",
                                (shipment_id,),
                            ).fetchone()
                        )
                    if allocation_id is not None:
                        self.assertIsNotNone(
                            conn.execute(
                                "SELECT id FROM assembly_shipment_allocations WHERE id = ?",
                                (allocation_id,),
                            ).fetchone()
                        )

    def test_order_delete_blocks_direct_inventory_history_reference(self):
        manual_id = self.create_product("P-DELETE-INVENTORY", "客户A")
        order_id = self.create_order(manual_id, "SO-DELETE-INVENTORY", 100)
        self.stock_product(manual_id, 0)
        with app.get_db() as conn:
            location_id = conn.execute(
                "SELECT id FROM warehouse_locations ORDER BY id LIMIT 1"
            ).fetchone()["id"]
        inbound = self.client.post(
            "/admin/inventory/inbound",
            data={
                "manual_id": str(manual_id),
                "quantity": "10",
                "location_id": str(location_id),
                "stock_type": "生产入库",
                "related_order_id": str(order_id),
                "related_order_no": "SO-DELETE-INVENTORY",
                "remark": "订单入库历史",
            },
        )
        self.assertEqual(inbound.status_code, 302)

        response = self.client.post(
            f"/admin/orders/{order_id}/delete", follow_redirects=True
        )

        self.assertIn("已有库存流水的订单不能删除", response.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM product_orders WHERE id = ?", (order_id,)
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM inventory_transactions WHERE related_order_id = ?",
                    (str(order_id),),
                ).fetchone()
            )

    def test_order_delete_blocks_real_shipment_plan_item_without_orphans(self):
        manual_id = self.create_product("P-DELETE-PLAN", "客户A")
        order_id = self.create_order(manual_id, "SO-DELETE-PLAN", 100)
        created = self.client.post(
            "/admin/orders/shipment-plans",
            data={
                "order_id": [str(order_id)],
                "planned_ship_at": "2026-09-01",
            },
        )
        self.assertEqual(created.status_code, 302)
        with app.get_db() as conn:
            plan_item = conn.execute(
                """
                SELECT shipment_plan_items.id, shipment_plan_items.plan_id,
                       shipment_plans.status
                FROM shipment_plan_items
                JOIN shipment_plans ON shipment_plans.id = shipment_plan_items.plan_id
                WHERE shipment_plan_items.order_id = ?
                """,
                (order_id,),
            ).fetchone()
        self.assertIsNotNone(plan_item)
        self.assertEqual(plan_item["status"], "待发货")

        response = self.client.post(
            f"/admin/orders/{order_id}/delete", follow_redirects=True
        )

        self.assertIn("已有计划发货记录的订单不能删除", response.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertIsNotNone(
                conn.execute(
                    "SELECT id FROM product_orders WHERE id = ?", (order_id,)
                ).fetchone()
            )
            persisted_item = conn.execute(
                """
                SELECT order_id
                FROM shipment_plan_items
                WHERE id = ? AND plan_id = ?
                """,
                (plan_item["id"], plan_item["plan_id"]),
            ).fetchone()
            persisted_plan = conn.execute(
                "SELECT status FROM shipment_plans WHERE id = ?",
                (plan_item["plan_id"],),
            ).fetchone()
        self.assertEqual(persisted_item["order_id"], order_id)
        self.assertEqual(persisted_plan["status"], "待发货")

    def test_failure_after_first_order_summary_update_rolls_everything_back(self):
        first = self.create_product("P-SUMMARY-ROLLBACK-1", "客户A")
        second = self.create_product("P-SUMMARY-ROLLBACK-2", "客户A")
        self.configure_components([(first, 1), (second, 1)])
        first_order = self.create_order(first, "SO-SUMMARY-ROLLBACK-1", 10)
        second_order = self.create_order(second, "SO-SUMMARY-ROLLBACK-2", 10)
        self.stock_product(first, 10)
        self.stock_product(second, 10)
        preview = self.post_preview(sets=10).get_json()
        original_sync = app.sync_order_shipment_summary
        sync_calls = 0

        def fail_on_second_summary(conn, order_id, updated_at=""):
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise sqlite3.DatabaseError("injected summary failure")
            return original_sync(conn, order_id, updated_at=updated_at)

        with patch.object(
            app, "sync_order_shipment_summary", side_effect=fail_on_second_summary
        ):
            response = self.post_save(preview)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(sync_calls, 2)
        with app.get_db() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                )
            }
            transactions = conn.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE related_order_type = '组装发货'"
            ).fetchone()[0]
            balances = {
                manual_id: app.inventory_total_for_manual(conn, manual_id)
                for manual_id in (first, second)
            }
            orders = conn.execute(
                "SELECT id, shipped_quantity, shipped_at FROM product_orders ORDER BY id"
            ).fetchall()
        self.assertEqual(counts, {table: 0 for table in counts})
        self.assertEqual(transactions, 0)
        self.assertEqual(balances, {first: 10, second: 10})
        self.assertEqual(
            [(row["id"], row["shipped_quantity"], row["shipped_at"]) for row in orders],
            [(first_order, 0, ""), (second_order, 0, "")],
        )

    def test_order_summary_sync_remains_compatible_without_assembly_tables(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE product_orders (
                    id INTEGER PRIMARY KEY,
                    shipped_quantity INTEGER NOT NULL DEFAULT 0,
                    shipped_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE product_order_shipments (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    shipped_quantity INTEGER NOT NULL,
                    shipped_at TEXT NOT NULL
                );
                INSERT INTO product_orders (id) VALUES (10);
                INSERT INTO product_order_shipments (
                    id, order_id, shipped_quantity, shipped_at
                ) VALUES (1, 10, 4, '2026-08-30');
                """
            )

            app.sync_order_shipment_summary(conn, 10, updated_at="now")
            row = conn.execute("SELECT * FROM product_orders WHERE id = 10").fetchone()
        finally:
            conn.close()

        self.assertEqual(
            (row["shipped_quantity"], row["shipped_at"], row["updated_at"]),
            (4, "2026-08-30", "now"),
        )


class AssemblyTask7TestCase(AssemblyAppTestCase):
    def setUp(self):
        super().setUp()
        self.original_shipment_images_dir = app.SHIPMENT_IMAGES_DIR
        app.SHIPMENT_IMAGES_DIR = Path(self.tmpdir.name) / "shipment-images"
        app.SHIPMENT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        app.SHIPMENT_IMAGES_DIR = self.original_shipment_images_dir
        super().tearDown()

    def configure_components(self, components, assembly_drawing_no="ASM-100"):
        now = "2026-08-30T10:00:00"
        with app.get_db() as conn:
            for sort_order, (manual_id, quantity_per_set) in enumerate(components):
                app.save_product_assembly_components(
                    conn,
                    manual_id,
                    [
                        {
                            "assembly_drawing_no": assembly_drawing_no,
                            "quantity_per_set": quantity_per_set,
                            "sort_order": sort_order,
                        }
                    ],
                    now,
                )

    def save_data(
        self,
        preview,
        *,
        quantities=None,
        preview_token=None,
        confirm_warnings="1",
        extra_data=None,
    ):
        items = preview["items"]
        data = {
            "customer": preview["customer"],
            "assembly_drawing_no": preview["assembly_drawing_no"],
            "set_quantity": str(preview["set_quantity"]),
            "manual_id": [str(item["manual_id"]) for item in items],
            "shipped_quantity": quantities
            if quantities is not None
            else [str(item["shipped_quantity"]) for item in items],
            "shipped_at": "2026-08-30",
            "logistics_no": "历史批次备注",
            "preview_token": preview["preview_token"]
            if preview_token is None
            else preview_token,
            "confirm_warnings": confirm_warnings,
        }
        if extra_data:
            data.update(extra_data)
        return data

    def create_batch(
        self,
        *,
        drawing_prefix="P-HISTORY",
        assembly_drawing_no="ASM-100",
        set_quantity=100,
        shipped_at="2026-08-30",
    ):
        first = self.create_product(f"{drawing_prefix}-1", "客户A")
        second = self.create_product(f"{drawing_prefix}-2", "客户A")
        self.configure_components(
            [(first, 2), (second, 3)],
            assembly_drawing_no=assembly_drawing_no,
        )
        first_order = self.create_order(first, f"SO-{drawing_prefix}-1", 140)
        second_order = self.create_order(second, f"SO-{drawing_prefix}-2", 300)
        self.stock_product(first, 200)
        self.stock_product(second, 270)
        preview_response = self.post_preview(
            customer="客户A",
            assembly=assembly_drawing_no,
            sets=set_quantity,
        )
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.get_json()
        response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=self.save_data(
                preview,
                extra_data={"shipped_at": shipped_at},
            ),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["batch_id"], (first, second), (first_order, second_order)

    def create_single_batch(
        self,
        *,
        drawing_no="P-LIFECYCLE",
        quantity=100,
        order_quantity=100,
        stock_quantity=100,
        planned_ship_at="2026-09-02",
        images=None,
    ):
        manual_id = self.create_product(drawing_no, "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        order_id = self.create_order(
            manual_id,
            f"SO-{drawing_no}",
            order_quantity,
            planned_ship_at=planned_ship_at,
        )
        self.stock_product(manual_id, stock_quantity)
        preview = self.post_preview(sets=quantity).get_json()
        data = self.save_data(preview)
        if images is not None:
            data["images"] = images
        response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["batch_id"], manual_id, order_id

    def edit_preview(self, batch_id, manual_id, quantity):
        response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            json={"overrides": {str(manual_id): str(quantity)}},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def post_edit(
        self,
        batch_id,
        preview,
        *,
        token=None,
        confirm_warnings="1",
        images=None,
    ):
        data = {
            "manual_id": [str(item["manual_id"]) for item in preview["items"]],
            "shipped_quantity": [
                str(item["shipped_quantity"]) for item in preview["items"]
            ],
            "shipped_at": "2026-08-31",
            "logistics_no": "修改后备注",
            "preview_token": preview["preview_token"] if token is None else token,
            "confirm_warnings": confirm_warnings,
        }
        if images is not None:
            data["images"] = images
        return self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            data=data,
            content_type="multipart/form-data",
        )

    def batch_state(self, batch_id, manual_id, order_ids):
        with app.get_db() as conn:
            batch = conn.execute(
                "SELECT * FROM assembly_shipment_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            items = conn.execute(
                "SELECT * FROM assembly_shipment_items WHERE batch_id = ? ORDER BY id",
                (batch_id,),
            ).fetchall()
            item_ids = [row["id"] for row in items]
            allocations = []
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                allocations = conn.execute(
                    f"SELECT * FROM assembly_shipment_allocations WHERE item_id IN ({placeholders}) ORDER BY id",
                    item_ids,
                ).fetchall()
            transactions = conn.execute(
                "SELECT * FROM inventory_transactions WHERE related_order_type = '组装发货' ORDER BY id"
            ).fetchall()
            placeholders = ",".join("?" for _ in order_ids)
            orders = conn.execute(
                f"SELECT * FROM product_orders WHERE id IN ({placeholders}) ORDER BY id",
                list(order_ids),
            ).fetchall()
            images = conn.execute(
                "SELECT * FROM assembly_shipment_images WHERE batch_id = ? ORDER BY id",
                (batch_id,),
            ).fetchall()
            balance = app.inventory_total_for_manual(conn, manual_id)
        return {
            "batch": dict(batch) if batch else None,
            "items": [dict(row) for row in items],
            "allocations": [dict(row) for row in allocations],
            "transactions": [dict(row) for row in transactions],
            "orders": [dict(row) for row in orders],
            "images": [dict(row) for row in images],
            "balance": balance,
        }


class AssemblyHistoryTests(AssemblyTask7TestCase):
    def test_history_renders_grouped_immutable_batch_details_before_ordinary_rows(self):
        batch_id, manuals, orders = self.create_batch()
        ordinary_manual = self.create_product("P-ORDINARY-HISTORY", "客户B")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-ORDINARY-HISTORY", 10, customer="客户B"
        )
        self.create_shipment(ordinary_order, 2, shipped_at="2026-08-29")
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET drawing_no = 'P-CHANGED', product_name = '已修改产品' WHERE id = ?",
                (manuals[0],),
            )

        html = self.client.get("/admin/shipped-orders").get_data(as_text=True)

        self.assertIn(f'data-assembly-batch-id="{batch_id}"', html)
        self.assertIn('data-shipment-history-count>2</strong>', html)
        self.assertLess(html.index("组装发货批次"), html.index("SO-ORDINARY-HISTORY"))
        self.assertIn("ASM-100", html)
        self.assertIn("100 套", html)
        self.assertIn("P-HISTORY-1", html)
        self.assertIn("P-HISTORY-2", html)
        self.assertIn(f"SO-P-HISTORY-1", html)
        self.assertIn(f"SO-P-HISTORY-2", html)
        self.assertIn("无订单直接发货", html)
        self.assertIn("库存缺口 30", html)
        self.assertIn("实际扣减 270", html)
        self.assertNotIn("已修改产品", html)

    def test_history_filters_batches_without_changing_ordinary_results(self):
        self.create_batch()
        ordinary_manual = self.create_product("P-ORDINARY-FILTER", "客户B")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-ORDINARY-FILTER", 10, customer="客户B"
        )
        self.create_shipment(ordinary_order, 2, shipped_at="2026-08-29")

        cases = [
            ({"customer": "客户A"}, True, False),
            ({"customer": "客户B"}, False, True),
            ({"q": "ASM-100"}, True, False),
            ({"q": "P-HISTORY-2"}, True, False),
            ({"q": "产品-P-HISTORY-1"}, True, False),
            ({"shipped_at": "2026-08-30"}, True, False),
            ({"shipped_at": "2026-08-29"}, False, True),
            ({"q": "SO-ORDINARY-FILTER"}, False, True),
        ]
        for query_string, has_batch, has_ordinary in cases:
            with self.subTest(query_string=query_string):
                html = self.client.get(
                    "/admin/shipped-orders", query_string=query_string
                ).get_data(as_text=True)
                self.assertEqual("ASM-100" in html, has_batch)
                self.assertEqual(
                    '<td data-label="订单号">SO-ORDINARY-FILTER</td>' in html,
                    has_ordinary,
                )

    def test_batch_fetch_uses_one_parent_bound_query_per_child_relation(self):
        self.create_batch(drawing_prefix="P-BULK-A", assembly_drawing_no="ASM-BULK-A")
        self.create_batch(drawing_prefix="P-BULK-B", assembly_drawing_no="ASM-BULK-B")
        calls = []
        with app.get_db() as conn:
            class RecordingConnection:
                def execute(self, sql, params=()):
                    calls.append((sql, tuple(params)))
                    return conn.execute(sql, params)

            batches = app.fetch_assembly_shipment_batches(RecordingConnection())

        selects = [call for call in calls if "assembly_shipment_" in call[0]]
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(selects), 4, "\n\n".join(sql for sql, params in selects))
        self.assertEqual(
            sum("FROM assembly_shipment_items" in sql for sql, params in selects),
            1,
        )
        self.assertEqual(
            sum("FROM assembly_shipment_allocations" in sql for sql, params in selects),
            1,
        )
        self.assertEqual(
            sum("FROM assembly_shipment_images" in sql for sql, params in selects),
            1,
        )
        selected_batch_ids = tuple(batch["id"] for batch in batches)
        for relation in (
            "FROM assembly_shipment_items",
            "FROM assembly_shipment_allocations",
            "FROM assembly_shipment_images",
        ):
            sql, params = next(call for call in selects if relation in call[0])
            with self.subTest(relation=relation):
                self.assertEqual(params, selected_batch_ids)
                self.assertEqual(sql.count("?"), len(selected_batch_ids))


class AssemblyEditDeleteTests(AssemblyTask7TestCase):
    def test_edit_preserves_retained_item_price_snapshots_after_current_prices_change(self):
        first = self.create_product("P-EDIT-PRICE-1", "客户A")
        second = self.create_product("P-EDIT-PRICE-2", "客户A")
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 100, currency = 'CNY' WHERE id = ?",
                (first,),
            )
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 250, currency = 'CNY' WHERE id = ?",
                (second,),
            )
        self.configure_components([(first, 2), (second, 3)])
        self.create_order(first, "SO-EDIT-PRICE-1", 4)
        self.create_order(second, "SO-EDIT-PRICE-2", 6)
        self.stock_product(first, 4)
        self.stock_product(second, 6)
        create_preview = self.post_preview(sets=1).get_json()
        create_response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=self.save_data(create_preview),
        )
        self.assertEqual(
            create_response.status_code,
            201,
            create_response.get_data(as_text=True),
        )
        batch_id = create_response.get_json()["batch_id"]
        with app.get_db() as conn:
            original = {
                row["manual_id"]: (
                    row["unit_price_minor"],
                    row["currency"],
                    row["price_recorded_by"],
                    row["price_recorded_at"],
                )
                for row in conn.execute(
                    "SELECT * FROM assembly_shipment_items WHERE batch_id = ?",
                    (batch_id,),
                )
            }
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 999 WHERE id IN (?, ?)",
                (first, second),
            )
        self.assertEqual(
            {
                manual_id: (snapshot[0], snapshot[1])
                for manual_id, snapshot in original.items()
            },
            {first: (100, "CNY"), second: (250, "CNY")},
        )

        preview_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            json={"overrides": {str(first): "4", str(second): "6"}},
        )
        self.assertEqual(preview_response.status_code, 200)
        edit_response = self.post_edit(batch_id, preview_response.get_json())

        self.assertEqual(
            edit_response.status_code,
            201,
            edit_response.get_data(as_text=True),
        )
        with app.get_db() as conn:
            edited = {
                row["manual_id"]: (
                    row["unit_price_minor"],
                    row["currency"],
                    row["price_recorded_by"],
                    row["price_recorded_at"],
                )
                for row in conn.execute(
                    "SELECT * FROM assembly_shipment_items WHERE batch_id = ?",
                    (batch_id,),
                )
            }
        self.assertEqual(edited, original)

    def test_edit_uses_current_price_only_for_a_newly_introduced_component(self):
        retained = self.create_product("P-PRICE-RETAINED", "客户A")
        removed = self.create_product("P-PRICE-REMOVED", "客户A")
        introduced = self.create_product("P-PRICE-NEW", "客户A")
        with app.get_db() as conn:
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 100 WHERE id = ?",
                (retained,),
            )
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 200 WHERE id = ?",
                (removed,),
            )
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 300 WHERE id = ?",
                (introduced,),
            )
        self.configure_components([(retained, 1), (removed, 1)])
        self.create_order(retained, "SO-PRICE-RETAINED", 2)
        self.create_order(removed, "SO-PRICE-REMOVED", 1)
        self.create_order(introduced, "SO-PRICE-NEW", 1)
        self.stock_product(retained, 2)
        self.stock_product(removed, 1)
        self.stock_product(introduced, 1)
        create_preview = self.post_preview(sets=1).get_json()
        create_response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=self.save_data(create_preview),
        )
        self.assertEqual(create_response.status_code, 201)
        batch_id = create_response.get_json()["batch_id"]

        with app.get_db() as conn:
            app.save_product_assembly_components(
                conn,
                removed,
                [],
                "2026-08-30T12:00:00",
            )
            app.save_product_assembly_components(
                conn,
                introduced,
                [
                    {
                        "assembly_drawing_no": "ASM-100",
                        "quantity_per_set": 1,
                        "sort_order": 1,
                    }
                ],
                "2026-08-30T12:00:00",
            )
            conn.execute(
                "UPDATE manuals SET unit_price_minor = 999 WHERE id = ?",
                (retained,),
            )
        preview_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            json={"overrides": {str(retained): "1", str(introduced): "1"},
                  "selected_manual_ids": [retained, introduced]},
        )
        self.assertEqual(preview_response.status_code, 200)

        edit_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            data=self.save_data(preview_response.get_json(), extra_data={
                "selected_manual_ids": [str(retained), str(introduced)],
            }),
        )

        self.assertEqual(
            edit_response.status_code,
            201,
            edit_response.get_data(as_text=True),
        )
        with app.get_db() as conn:
            prices = {
                row["manual_id"]: row["unit_price_minor"]
                for row in conn.execute(
                    """
                    SELECT manual_id, unit_price_minor
                    FROM assembly_shipment_items
                    WHERE batch_id = ?
                    """,
                    (batch_id,),
                )
            }
        self.assertEqual(prices, {retained: 100, introduced: 300})

    def test_edit_adds_back_own_batch_once_and_preserves_batch_identity(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-EDIT-DOWN",
            quantity=200,
            order_quantity=200,
            stock_quantity=170,
        )

        get_response = self.client.get(
            f"/admin/shipped-orders/assembly/{batch_id}/edit"
        )
        preview_response = self.edit_preview(batch_id, manual_id, 120)

        self.assertEqual(get_response.status_code, 200)
        self.assertIn("修改组装发货", get_response.get_data(as_text=True))
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.get_json()
        item = preview["items"][0]
        self.assertEqual(item["available_inventory"], 170)
        self.assertEqual(item["inventory_shortage_quantity"], 0)
        self.assertEqual(
            item["allocations"],
            [{"order_id": order_id, "order_no": "SO-P-EDIT-DOWN", "quantity": 120}],
        )

        response = self.post_edit(batch_id, preview)

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["batch_id"], batch_id)
        with app.get_db() as conn:
            batches = conn.execute(
                "SELECT id, shipped_at, logistics_no FROM assembly_shipment_batches"
            ).fetchall()
            item = conn.execute(
                "SELECT * FROM assembly_shipment_items WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            order = conn.execute(
                "SELECT shipped_quantity FROM product_orders WHERE id = ?", (order_id,)
            ).fetchone()
            balance = app.inventory_total_for_manual(conn, manual_id)
        self.assertEqual(
            [(row["id"], row["shipped_at"], row["logistics_no"]) for row in batches],
            [(batch_id, "2026-08-31", "修改后备注")],
        )
        self.assertEqual(
            (
                item["shipped_quantity"],
                item["inventory_deducted_quantity"],
                item["inventory_shortage_quantity"],
                balance,
                order["shipped_quantity"],
            ),
            (120, 120, 0, 50, 120),
        )

    def test_edit_reallocates_and_resyncs_union_of_old_and_new_orders(self):
        batch_id, manual_id, old_order_id = self.create_single_batch(
            drawing_no="P-EDIT-ORDERS",
            quantity=80,
            order_quantity=80,
            stock_quantity=200,
            planned_ship_at="2026-09-02",
        )
        new_order_id = self.create_order(
            manual_id,
            "SO-P-EDIT-ORDERS-NEW",
            60,
            planned_ship_at="2026-09-01",
        )
        preview = self.edit_preview(batch_id, manual_id, 120).get_json()

        self.assertEqual(
            preview["items"][0]["allocations"],
            [
                {
                    "order_id": new_order_id,
                    "order_no": "SO-P-EDIT-ORDERS-NEW",
                    "quantity": 60,
                },
                {
                    "order_id": old_order_id,
                    "order_no": "SO-P-EDIT-ORDERS",
                    "quantity": 60,
                },
            ],
        )
        response = self.post_edit(batch_id, preview)

        self.assertEqual(response.status_code, 201)
        with app.get_db() as conn:
            orders = conn.execute(
                "SELECT id, shipped_quantity, shipped_at FROM product_orders WHERE id IN (?, ?) ORDER BY id",
                (old_order_id, new_order_id),
            ).fetchall()
        self.assertEqual(
            {
                row["id"]: (row["shipped_quantity"], row["shipped_at"])
                for row in orders
            },
            {
                old_order_id: (60, "2026-08-31"),
                new_order_id: (60, "2026-08-31"),
            },
        )

    def test_edit_stale_token_and_unconfirmed_warnings_save_nothing(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-EDIT-WARN",
            quantity=100,
            order_quantity=100,
            stock_quantity=100,
        )
        preview = self.edit_preview(batch_id, manual_id, 150).get_json()
        before = self.batch_state(batch_id, manual_id, [order_id])

        stale = self.post_edit(batch_id, preview, token="stale-token")
        unconfirmed = self.post_edit(
            batch_id, preview, confirm_warnings="0"
        )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(unconfirmed.status_code, 409)
        self.assertEqual(
            {warning["code"] for warning in unconfirmed.get_json()["preview"]["warnings"]},
            {"no_order", "inventory_shortage"},
        )
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)

        confirmed = self.post_edit(batch_id, preview, confirm_warnings="1")

        self.assertEqual(confirmed.status_code, 201)
        with app.get_db() as conn:
            item = conn.execute(
                "SELECT * FROM assembly_shipment_items WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            allocations = conn.execute(
                """
                SELECT order_id, quantity
                FROM assembly_shipment_allocations
                WHERE item_id = ?
                ORDER BY id
                """,
                (item["id"],),
            ).fetchall()
        self.assertEqual(
            (
                item["shipped_quantity"],
                item["inventory_deducted_quantity"],
                item["inventory_shortage_quantity"],
            ),
            (150, 100, 50),
        )
        self.assertEqual(
            [(row["order_id"], row["quantity"]) for row in allocations],
            [(order_id, 100), (None, 50)],
        )

    def test_edit_rejects_incomplete_duplicate_and_unknown_item_maps(self):
        first = self.create_product("P-EDIT-MAP-1", "客户A")
        second = self.create_product("P-EDIT-MAP-2", "客户A")
        self.configure_components([(first, 1), (second, 1)])
        first_order = self.create_order(first, "SO-EDIT-MAP-1", 10)
        second_order = self.create_order(second, "SO-EDIT-MAP-2", 10)
        self.stock_product(first, 10)
        self.stock_product(second, 10)
        create_preview = self.post_preview(sets=10).get_json()
        create_response = self.client.post(
            "/admin/shipped-orders/assembly/new", data=self.save_data(create_preview)
        )
        batch_id = create_response.get_json()["batch_id"]
        preview_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/edit",
            json={"overrides": {str(first): "10", str(second): "10"}},
        )
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.get_json()
        base = {
            "shipped_at": "2026-08-31",
            "logistics_no": "map",
            "preview_token": preview["preview_token"],
            "confirm_warnings": "1",
        }
        cases = [
            ([str(first)], ["10"]),
            ([str(first), str(first)], ["10", "10"]),
            ([str(first), "999999"], ["10", "10"]),
            ([str(first), str(second)], ["10"]),
        ]
        before = self.batch_state(
            batch_id, first, [first_order, second_order]
        )
        for manual_ids, quantities in cases:
            with self.subTest(manual_ids=manual_ids, quantities=quantities):
                response = self.client.post(
                    f"/admin/shipped-orders/assembly/{batch_id}/edit",
                    data={
                        **base,
                        "manual_id": manual_ids,
                        "shipped_quantity": quantities,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    self.batch_state(
                        batch_id, first, [first_order, second_order]
                    ),
                    before,
                )

    def test_edit_failure_after_reversal_rolls_back_exact_prior_state(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-EDIT-REVERSE-FAIL",
            quantity=100,
            order_quantity=100,
            stock_quantity=100,
        )
        preview = self.edit_preview(batch_id, manual_id, 80).get_json()
        before = self.batch_state(batch_id, manual_id, [order_id])
        original_reverse = getattr(app, "reverse_assembly_shipment_batch", None)

        def reverse_then_fail(conn, target_batch_id):
            if original_reverse is not None:
                original_reverse(conn, target_batch_id)
            raise sqlite3.DatabaseError("injected after reversal")

        with patch.object(
            app,
            "reverse_assembly_shipment_batch",
            side_effect=reverse_then_fail,
            create=True,
        ):
            response = self.post_edit(batch_id, preview)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)

    def test_edit_failure_after_replacement_writes_rolls_back_exact_prior_state(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-EDIT-WRITE-FAIL",
            quantity=100,
            order_quantity=100,
            stock_quantity=100,
        )
        preview = self.edit_preview(batch_id, manual_id, 80).get_json()
        before = self.batch_state(batch_id, manual_id, [order_id])
        original_sync = app.sync_order_shipment_summary

        def sync_then_fail(conn, target_order_id, updated_at=""):
            original_sync(conn, target_order_id, updated_at=updated_at)
            raise sqlite3.DatabaseError("injected after replacement writes")

        with patch.object(
            app, "sync_order_shipment_summary", side_effect=sync_then_fail
        ):
            response = self.post_edit(batch_id, preview)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)

    def test_delete_restores_only_exact_deduction_and_resyncs_order(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-DELETE",
            quantity=200,
            order_quantity=140,
            stock_quantity=170,
        )
        response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/delete"
        )

        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                    "assembly_shipment_images",
                )
            }
            balance = app.inventory_total_for_manual(conn, manual_id)
            order = conn.execute(
                "SELECT shipped_quantity, shipped_at FROM product_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            transactions = conn.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE related_order_type = '组装发货'"
            ).fetchone()[0]
        self.assertEqual(counts, {table: 0 for table in counts})
        self.assertEqual(balance, 170)
        self.assertEqual((order["shipped_quantity"], order["shipped_at"]), (0, ""))
        self.assertEqual(transactions, 0)

    def test_delete_failure_after_reversal_rolls_back_and_missing_batches_are_404(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-DELETE-FAIL",
            images=(BytesIO(VALID_PNG_BYTES), "delete-rollback.png", "image/png"),
        )
        before = self.batch_state(batch_id, manual_id, [order_id])
        before_files = {
            path.name: path.read_bytes() for path in app.SHIPMENT_IMAGES_DIR.iterdir()
        }
        original_reverse = getattr(app, "reverse_assembly_shipment_batch", None)

        def reverse_then_fail(conn, target_batch_id):
            if original_reverse is not None:
                original_reverse(conn, target_batch_id)
            raise sqlite3.DatabaseError("injected delete failure")

        with patch.object(
            app,
            "reverse_assembly_shipment_batch",
            side_effect=reverse_then_fail,
            create=True,
        ):
            response = self.client.post(
                f"/admin/shipped-orders/assembly/{batch_id}/delete"
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)
        self.assertEqual(
            {path.name: path.read_bytes() for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            before_files,
        )
        self.assertEqual(
            self.client.get("/admin/shipped-orders/assembly/999999/edit").status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/admin/shipped-orders/assembly/999999/delete").status_code,
            404,
        )

    def test_edit_and_delete_require_shipped_manage_permission(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-LIFECYCLE-PERMISSION"
        )
        now = "2026-08-30T13:00:00"
        with app.get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_view_shipped, can_manage_shipped, created_at, updated_at
                ) VALUES ('viewer-lifecycle', 'unused', 'operator', 1, 1, 0, ?, ?)
                """,
                (now, now),
            )
        with self.client.session_transaction() as session:
            session["admin_username"] = "viewer-lifecycle"
            session["admin_role"] = "operator"

        edit = self.client.get(
            f"/admin/shipped-orders/assembly/{batch_id}/edit"
        )
        delete = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/delete"
        )

        self.assertEqual(edit.status_code, 302)
        self.assertEqual(delete.status_code, 302)
        self.assertIsNotNone(self.batch_state(batch_id, manual_id, [order_id])["batch"])


class AssemblyAcceptanceTests(AssemblyAppTestCase):
    def test_100_sets_support_manual_override_fifo_direct_shipping_and_stock_floor(self):
        p1_id = self.create_product("P1", "客户A")
        p2_id = self.create_product("P2", "客户A")
        self.configure_component(p1_id, quantity_per_set=2, sort_order=0)
        self.configure_component(p2_id, quantity_per_set=3, sort_order=1)

        first_order_id = self.create_order(
            p1_id,
            "SO-P1-FIRST",
            130,
            planned_ship_at="2026-09-01",
            ordered_at="2026-08-01",
        )
        second_order_id = self.create_order(
            p1_id,
            "SO-P1-SECOND",
            60,
            planned_ship_at="2026-09-02",
            ordered_at="2026-08-02",
        )
        ordinary_shipment_id = self.create_shipment(
            first_order_id, 10, shipped_at="2026-08-29"
        )
        self.stock_product(p1_id, 220)
        self.stock_product(p2_id, 250)
        with app.get_db() as conn:
            app.sync_order_shipment_summary(
                conn, first_order_id, updated_at="2026-08-30T09:45:00"
            )
            original_balances = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT manual_id, location_id, quantity
                    FROM inventory_balances
                    WHERE manual_id IN (?, ?)
                    ORDER BY manual_id, location_id
                    """,
                    (p1_id, p2_id),
                ).fetchall()
            ]

        default_response = self.post_preview(sets=100)
        self.assertEqual(default_response.status_code, 200)
        default_items = {
            item["drawing_no"]: item for item in default_response.get_json()["items"]
        }
        self.assertEqual(
            (
                default_items["P1"]["calculated_quantity"],
                default_items["P1"]["shipped_quantity"],
                default_items["P2"]["calculated_quantity"],
                default_items["P2"]["shipped_quantity"],
            ),
            (200, 200, 300, 300),
        )

        override_response = self.post_preview(
            sets=100, overrides={str(p2_id): "280"}
        )
        self.assertEqual(override_response.status_code, 200)
        preview = override_response.get_json()
        preview_items = {item["drawing_no"]: item for item in preview["items"]}
        self.assertEqual(
            (
                preview_items["P1"]["calculated_quantity"],
                preview_items["P1"]["shipped_quantity"],
                preview_items["P2"]["calculated_quantity"],
                preview_items["P2"]["shipped_quantity"],
            ),
            (200, 200, 300, 280),
        )
        self.assertEqual(
            preview_items["P1"]["allocations"],
            [
                {
                    "order_id": first_order_id,
                    "order_no": "SO-P1-FIRST",
                    "quantity": 120,
                },
                {
                    "order_id": second_order_id,
                    "order_no": "SO-P1-SECOND",
                    "quantity": 60,
                },
                {"order_id": None, "order_no": "", "quantity": 20},
            ],
        )
        self.assertEqual(
            preview_items["P2"]["allocations"],
            [{"order_id": None, "order_no": "", "quantity": 280}],
        )
        self.assertEqual(
            (
                preview_items["P2"]["available_inventory"],
                preview_items["P2"]["inventory_deducted_quantity"],
                preview_items["P2"]["inventory_shortage_quantity"],
            ),
            (250, 250, 30),
        )

        save_data = {
            "customer": preview["customer"],
            "assembly_drawing_no": preview["assembly_drawing_no"],
            "set_quantity": str(preview["set_quantity"]),
            "manual_id": [str(item["manual_id"]) for item in preview["items"]],
            "shipped_quantity": [
                str(item["shipped_quantity"]) for item in preview["items"]
            ],
            "shipped_at": "2026-08-30",
            "logistics_no": "100套验收批次",
            "preview_token": preview["preview_token"],
            "confirm_warnings": "1",
        }

        original_sync = app.sync_order_shipment_summary
        sync_calls = 0

        def fail_after_first_summary_write(conn, order_id, updated_at=""):
            nonlocal sync_calls
            sync_calls += 1
            original_sync(conn, order_id, updated_at=updated_at)
            raise sqlite3.DatabaseError("injected acceptance mid-save failure")

        with patch.object(
            app,
            "sync_order_shipment_summary",
            side_effect=fail_after_first_summary_write,
        ):
            failed_response = self.client.post(
                "/admin/shipped-orders/assembly/new", data=save_data
            )

        self.assertEqual(failed_response.status_code, 500)
        self.assertEqual(sync_calls, 1)
        with app.get_db() as conn:
            failed_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                    "assembly_shipment_images",
                )
            }
            failed_transaction_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM inventory_transactions
                WHERE related_order_type = '组装发货'
                """
            ).fetchone()[0]
            failed_balances = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT manual_id, location_id, quantity
                    FROM inventory_balances
                    WHERE manual_id IN (?, ?)
                    ORDER BY manual_id, location_id
                    """,
                    (p1_id, p2_id),
                ).fetchall()
            ]
            failed_orders = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT id, shipped_quantity, shipped_at
                    FROM product_orders
                    WHERE id IN (?, ?)
                    ORDER BY id
                    """,
                    (first_order_id, second_order_id),
                ).fetchall()
            ]
            ordinary_rows_after_failure = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT id, order_id, shipped_quantity, shipped_at
                    FROM product_order_shipments
                    ORDER BY id
                    """
                ).fetchall()
            ]

        self.assertEqual(failed_counts, {table: 0 for table in failed_counts})
        self.assertEqual(failed_transaction_count, 0)
        self.assertEqual(failed_balances, original_balances)
        self.assertEqual(
            failed_orders,
            [(first_order_id, 10, "2026-08-29"), (second_order_id, 0, "")],
        )
        self.assertEqual(
            ordinary_rows_after_failure,
            [(ordinary_shipment_id, first_order_id, 10, "2026-08-29")],
        )

        saved_response = self.client.post(
            "/admin/shipped-orders/assembly/new", data=save_data
        )
        self.assertEqual(
            saved_response.status_code, 201, saved_response.get_data(as_text=True)
        )
        batch_id = saved_response.get_json()["batch_id"]

        with app.get_db() as conn:
            batch = conn.execute(
                "SELECT * FROM assembly_shipment_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            items = conn.execute(
                """
                SELECT *
                FROM assembly_shipment_items
                WHERE batch_id = ?
                ORDER BY drawing_no
                """,
                (batch_id,),
            ).fetchall()
            allocations = conn.execute(
                """
                SELECT item.drawing_no, allocation.order_id, allocation.quantity
                FROM assembly_shipment_allocations AS allocation
                JOIN assembly_shipment_items AS item ON item.id = allocation.item_id
                WHERE item.batch_id = ?
                ORDER BY item.drawing_no, allocation.id
                """,
                (batch_id,),
            ).fetchall()
            allocation_invariants = conn.execute(
                """
                SELECT item.id, item.shipped_quantity,
                       COALESCE(SUM(allocation.quantity), 0) AS allocation_total
                FROM assembly_shipment_items AS item
                LEFT JOIN assembly_shipment_allocations AS allocation
                  ON allocation.item_id = item.id
                WHERE item.batch_id = ?
                GROUP BY item.id, item.shipped_quantity
                ORDER BY item.id
                """,
                (batch_id,),
            ).fetchall()
            balances_after_save = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT manual_id, location_id, quantity
                    FROM inventory_balances
                    WHERE manual_id IN (?, ?)
                    ORDER BY manual_id, location_id
                    """,
                    (p1_id, p2_id),
                ).fetchall()
            ]
            transaction_totals = {
                row["related_order_id"]: row["quantity"]
                for row in conn.execute(
                    """
                    SELECT related_order_id, SUM(quantity) AS quantity
                    FROM inventory_transactions
                    WHERE related_order_type = '组装发货'
                    GROUP BY related_order_id
                    """
                ).fetchall()
            }
            order_totals = conn.execute(
                """
                SELECT orders.id, orders.quantity, orders.shipped_quantity,
                       COALESCE((
                           SELECT SUM(shipment.shipped_quantity)
                           FROM product_order_shipments AS shipment
                           WHERE shipment.order_id = orders.id
                       ), 0) AS ordinary_total,
                       COALESCE((
                           SELECT SUM(allocation.quantity)
                           FROM assembly_shipment_allocations AS allocation
                           WHERE allocation.order_id = orders.id
                       ), 0) AS assembly_total
                FROM product_orders AS orders
                WHERE orders.id IN (?, ?)
                ORDER BY orders.id
                """,
                (first_order_id, second_order_id),
            ).fetchall()

        self.assertEqual(
            (
                batch["customer"],
                batch["assembly_drawing_no"],
                batch["set_quantity"],
                batch["shipped_at"],
            ),
            ("客户A", "ASM-100", 100, "2026-08-30"),
        )
        items_by_drawing = {item["drawing_no"]: item for item in items}
        self.assertEqual(
            (
                items_by_drawing["P1"]["quantity_per_set"],
                items_by_drawing["P1"]["calculated_quantity"],
                items_by_drawing["P1"]["shipped_quantity"],
                items_by_drawing["P1"]["inventory_deducted_quantity"],
                items_by_drawing["P1"]["inventory_shortage_quantity"],
            ),
            (2, 200, 200, 200, 0),
        )
        self.assertEqual(
            (
                items_by_drawing["P2"]["quantity_per_set"],
                items_by_drawing["P2"]["calculated_quantity"],
                items_by_drawing["P2"]["shipped_quantity"],
                items_by_drawing["P2"]["inventory_deducted_quantity"],
                items_by_drawing["P2"]["inventory_shortage_quantity"],
            ),
            (3, 300, 280, 250, 30),
        )
        self.assertEqual(
            [tuple(row) for row in allocations],
            [
                ("P1", first_order_id, 120),
                ("P1", second_order_id, 60),
                ("P1", None, 20),
                ("P2", None, 280),
            ],
        )
        self.assertTrue(
            all(
                row["allocation_total"] == row["shipped_quantity"]
                for row in allocation_invariants
            )
        )
        self.assertTrue(
            all(
                item["inventory_deducted_quantity"]
                + item["inventory_shortage_quantity"]
                == item["shipped_quantity"]
                for item in items
            )
        )
        self.assertTrue(all(quantity >= 0 for _, _, quantity in balances_after_save))
        self.assertEqual(
            {manual_id: quantity for manual_id, _, quantity in balances_after_save},
            {p1_id: 20, p2_id: 0},
        )
        self.assertEqual(
            transaction_totals,
            {
                app.assembly_inventory_related_id(items_by_drawing["P1"]["id"]): 200,
                app.assembly_inventory_related_id(items_by_drawing["P2"]["id"]): 250,
            },
        )
        self.assertEqual(
            [
                (
                    row["id"],
                    row["quantity"],
                    row["ordinary_total"],
                    row["assembly_total"],
                    row["shipped_quantity"],
                )
                for row in order_totals
            ],
            [
                (first_order_id, 130, 10, 120, 130),
                (second_order_id, 60, 0, 60, 60),
            ],
        )
        self.assertTrue(
            all(
                row["ordinary_total"] + row["assembly_total"] <= row["quantity"]
                for row in order_totals
            )
        )

        delete_response = self.client.post(
            f"/admin/shipped-orders/assembly/{batch_id}/delete"
        )
        self.assertEqual(delete_response.status_code, 302)
        with app.get_db() as conn:
            final_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "assembly_shipment_batches",
                    "assembly_shipment_items",
                    "assembly_shipment_allocations",
                    "assembly_shipment_images",
                )
            }
            final_transaction_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM inventory_transactions
                WHERE related_order_type = '组装发货'
                """
            ).fetchone()[0]
            final_balances = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT manual_id, location_id, quantity
                    FROM inventory_balances
                    WHERE manual_id IN (?, ?)
                    ORDER BY manual_id, location_id
                    """,
                    (p1_id, p2_id),
                ).fetchall()
            ]
            final_orders = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT id, shipped_quantity, shipped_at
                    FROM product_orders
                    WHERE id IN (?, ?)
                    ORDER BY id
                    """,
                    (first_order_id, second_order_id),
                ).fetchall()
            ]
            final_ordinary_shipments = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT id, order_id, shipped_quantity, shipped_at
                    FROM product_order_shipments
                    ORDER BY id
                    """
                ).fetchall()
            ]

        self.assertEqual(final_counts, {table: 0 for table in final_counts})
        self.assertEqual(final_transaction_count, 0)
        self.assertEqual(final_balances, original_balances)
        self.assertEqual(
            final_orders,
            [(first_order_id, 10, "2026-08-29"), (second_order_id, 0, "")],
        )
        self.assertEqual(
            final_ordinary_shipments,
            [(ordinary_shipment_id, first_order_id, 10, "2026-08-29")],
        )


class AssemblyImageLifecycleTests(AssemblyTask7TestCase):
    def test_runtime_uses_declared_pillow_instead_of_local_stub(self):
        self.assertEqual(app.PillowPackage.__version__, "12.3.0")
        self.assertNotEqual(
            Path(app.PillowImage.__file__).resolve().parent,
            (app.BASE_DIR / "PIL").resolve(),
        )
        self.assertIs(
            app.ImageReader.__init__.__globals__["Image"], app.PillowImage
        )
        self.assertIn(
            "Pillow==12.3.0",
            (app.BASE_DIR / "requirements.txt").read_text(encoding="utf-8"),
        )

    def prepare_image_batch(self, drawing_no="P-IMAGE", quantity=20):
        manual_id = self.create_product(drawing_no, "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        order_id = self.create_order(manual_id, f"SO-{drawing_no}", quantity)
        self.stock_product(manual_id, quantity)
        preview = self.post_preview(sets=quantity).get_json()
        return manual_id, order_id, preview

    def create_ordinary_shipment_with_token(self, drawing_no):
        manual_id = self.create_product(drawing_no, "客户A")
        order_id = self.create_order(manual_id, f"SO-{drawing_no}", 10)
        self.stock_product(manual_id, 10)
        response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(order_id)],
                "shipped_quantity": ["1"],
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        self.assertIsNotNone(shipment)
        self.assertTrue(shipment["photo_upload_token"])
        return manual_id, order_id, shipment

    def test_new_shipment_upload_rejects_every_webp_variant_with_clear_message(self):
        cases = [
            ("valid-static", VALID_STATIC_WEBP_BYTES),
            ("header-only", HEADER_ONLY_WEBP_BYTES),
            ("valid-animated", VALID_ANIMATED_WEBP_BYTES),
        ]
        for index, (label, payload) in enumerate(cases):
            with self.subTest(webp=label):
                manual_id, order_id, preview = self.prepare_image_batch(
                    f"P-WEBP-REJECT-{index}"
                )
                data = self.save_data(preview)
                data["images"] = (
                    BytesIO(payload),
                    f"{label}.webp",
                    "image/webp",
                )

                response = self.client.post(
                    "/admin/shipped-orders/assembly/new",
                    data=data,
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("不支持 WebP", response.get_json()["error"])
                self.assertIn("PNG、JPEG 或 GIF", response.get_json()["error"])
                with app.get_db() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_items WHERE manual_id = ?",
                            (manual_id,),
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_semantic_shell_png_jpeg_gif_fail_real_assembly_create_without_residue(self):
        cases = [
            ("png-without-required-palette", SEMANTIC_SHELL_PNG_BYTES, "png", "image/png"),
            ("jpeg-zero-sample-precision", SEMANTIC_SHELL_JPEG_BYTES, "jpg", "image/jpeg"),
            ("gif-without-any-color-table", SEMANTIC_SHELL_GIF_BYTES, "gif", "image/gif"),
        ]
        for index, (label, payload, suffix, content_type) in enumerate(cases):
            with self.subTest(format=label):
                manual_id, order_id, preview = self.prepare_image_batch(
                    f"P-SEMANTIC-SHELL-{index}"
                )
                data = self.save_data(preview)
                data["images"] = (
                    BytesIO(payload),
                    f"{label}.{suffix}",
                    content_type,
                )

                response = self.client.post(
                    "/admin/shipped-orders/assembly/new",
                    data=data,
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("安全的位图", response.get_json()["error"])
                with app.get_db() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_items WHERE manual_id = ?",
                            (manual_id,),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_images"
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_all_six_shipment_upload_routes_reject_semantic_shells_without_residue(self):
        assembly_manual, assembly_order, assembly_preview = self.prepare_image_batch(
            "P-SEMANTIC-SIX-ASSEMBLY-CREATE"
        )
        assembly_data = self.save_data(assembly_preview)
        assembly_data["images"] = (
            BytesIO(SEMANTIC_SHELL_PNG_BYTES),
            "assembly-shell.png",
            "image/png",
        )
        assembly_create = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=assembly_data,
            content_type="multipart/form-data",
        )
        self.assertEqual(assembly_create.status_code, 400)

        batch_id, edit_manual, edit_order = self.create_single_batch(
            drawing_no="P-SEMANTIC-SIX-ASSEMBLY-EDIT"
        )
        edit_before = self.batch_state(batch_id, edit_manual, [edit_order])
        edit_preview = self.edit_preview(batch_id, edit_manual, 90).get_json()
        assembly_edit = self.post_edit(
            batch_id,
            edit_preview,
            images=(
                BytesIO(SEMANTIC_SHELL_JPEG_BYTES),
                "assembly-edit-shell.jpg",
                "image/jpeg",
            ),
        )
        self.assertEqual(assembly_edit.status_code, 400)
        self.assertEqual(
            self.batch_state(batch_id, edit_manual, [edit_order]), edit_before
        )

        ordinary_manual = self.create_product("P-SEMANTIC-SIX-ORDINARY", "客户A")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-SEMANTIC-SIX-ORDINARY", 10
        )
        self.stock_product(ordinary_manual, 10)
        ordinary_create = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(ordinary_order)],
                "shipped_quantity": ["1"],
                "images": (
                    BytesIO(SEMANTIC_SHELL_GIF_BYTES),
                    "ordinary-shell.gif",
                    "image/gif",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", ordinary_create.get_data(as_text=True))

        _, _, shipment = self.create_ordinary_shipment_with_token(
            "P-SEMANTIC-SIX-UPLOAD"
        )
        admin_upload = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/images",
            data={
                "images": (
                    BytesIO(SEMANTIC_SHELL_PNG_BYTES),
                    "admin-shell.png",
                    "image/png",
                )
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", admin_upload.get_data(as_text=True))
        public_upload = self.client.post(
            f"/shipment-photos/{shipment['photo_upload_token']}",
            data={
                "images": (
                    BytesIO(SEMANTIC_SHELL_JPEG_BYTES),
                    "public-shell.jpg",
                    "image/jpeg",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(public_upload.status_code, 200)
        self.assertIn("图片格式不支持", public_upload.get_data(as_text=True))

        plan_manual = self.create_product("P-SEMANTIC-SIX-PLAN", "客户A")
        plan_order = self.create_order(plan_manual, "SO-SEMANTIC-SIX-PLAN", 10)
        self.stock_product(plan_manual, 10)
        self.client.post(
            "/admin/orders/shipment-plans",
            data={"order_id": [str(plan_order)], "planned_ship_at": "2026-09-01"},
        )
        with app.get_db() as conn:
            plan_item = conn.execute(
                "SELECT plan_id, id FROM shipment_plan_items WHERE order_id = ?",
                (plan_order,),
            ).fetchone()
        plan_approve = self.client.post(
            f"/admin/shipped-orders/plans/{plan_item['plan_id']}/approve",
            data={
                "shipped_at": "2026-08-30",
                "plan_item_id": [str(plan_item["id"])],
                "shipped_quantity": ["1"],
                "images": (
                    BytesIO(SEMANTIC_SHELL_GIF_BYTES),
                    "plan-shell.gif",
                    "image/gif",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", plan_approve.get_data(as_text=True))

        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM product_order_shipment_images"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_images"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_batches"
                ).fetchone()[0],
                1,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT id FROM product_order_shipments WHERE order_id = ?",
                    (ordinary_order,),
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT id FROM product_order_shipments WHERE order_id = ?",
                    (plan_order,),
                ).fetchone()
            )
            persisted_plan_item = conn.execute(
                "SELECT shipped_quantity FROM shipment_plan_items WHERE id = ?",
                (plan_item["id"],),
            ).fetchone()
            remaining_inventory = {
                manual_id: app.inventory_total_for_manual(conn, manual_id)
                for manual_id in (ordinary_manual, plan_manual)
            }
        self.assertEqual(persisted_plan_item["shipped_quantity"], 0)
        self.assertEqual(
            remaining_inventory, {ordinary_manual: 10, plan_manual: 10}
        )
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_real_png_jpeg_static_and_animated_gif_pass_full_decode(self):
        cases = [
            ("png", VALID_PNG_BYTES, "png", "image/png"),
            ("jpeg", VALID_JPEG_BYTES, "jpg", "image/jpeg"),
            ("gif", VALID_GIF_BYTES, "gif", "image/gif"),
            ("animated-gif", VALID_ANIMATED_GIF_BYTES, "gif", "image/gif"),
        ]
        for index, (label, payload, suffix, content_type) in enumerate(cases):
            with self.subTest(format=label):
                manual_id, order_id, preview = self.prepare_image_batch(
                    f"P-REAL-RASTER-{index}"
                )
                data = self.save_data(preview)
                data["images"] = (
                    BytesIO(payload),
                    f"real-{label}.{suffix}",
                    content_type,
                )
                response = self.client.post(
                    "/admin/shipped-orders/assembly/new",
                    data=data,
                    content_type="multipart/form-data",
                )

                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                with app.get_db() as conn:
                    image = conn.execute(
                        "SELECT * FROM assembly_shipment_images WHERE batch_id = ?",
                        (response.get_json()["batch_id"],),
                    ).fetchone()
                self.assertEqual(image["content_type"], content_type)
                self.assertEqual(
                    (app.SHIPMENT_IMAGES_DIR / image["filename"]).read_bytes(), payload
                )

    def test_animated_gif_frame_limit_rejects_before_persistence(self):
        manual_id, order_id, preview = self.prepare_image_batch(
            "P-ANIMATED-GIF-FRAME-LIMIT"
        )
        data = self.save_data(preview)
        data["images"] = (
            BytesIO(VALID_ANIMATED_GIF_BYTES),
            "too-many-frames.gif",
            "image/gif",
        )
        with patch.dict(app.app.config, {"SHIPMENT_IMAGE_MAX_FRAMES": 1}):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=data,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_items WHERE manual_id = ?",
                    (manual_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_assembly_create_and_edit_reject_svg_and_disguised_active_content(self):
        active_payloads = [
            (
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                "attack.svg",
                "image/svg+xml",
            ),
            (b"<html><script>alert(1)</script></html>", "attack.png", "image/png"),
            (
                b"RIFF"
                + struct.pack("<I", 22)
                + b"WEBPVP8X"
                + struct.pack("<I", 10)
                + (b"\x00" * 10),
                "header-only.webp",
                "image/webp",
            ),
            (
                b"\xff\xd8"
                + b"\xff\xc0\x00\x08\x08\x00\x01\x00\x01\x01"
                + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
                + b"\xff\xd9",
                "header-only.jpg",
                "image/jpeg",
            ),
            (
                b"GIF89a\x01\x00\x01\x00\x00\x00\x00"
                + b",\x00\x00\x00\x00\x01\x00\x01\x00\x00"
                + b"\x02\x00;",
                "header-only.gif",
                "image/gif",
            ),
        ]
        for index, image in enumerate(active_payloads):
            with self.subTest(filename=image[1]):
                manual_id, order_id, preview = self.prepare_image_batch(
                    f"P-ACTIVE-CREATE-{index}"
                )
                data = self.save_data(preview)
                data["images"] = (BytesIO(image[0]), image[1], image[2])
                response = self.client.post(
                    "/admin/shipped-orders/assembly/new",
                    data=data,
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 400)
                expected_error = (
                    "不支持 WebP" if image[1].endswith(".webp") else "安全的位图"
                )
                self.assertIn(expected_error, response.get_json()["error"])
                with app.get_db() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_items WHERE manual_id = ?",
                            (manual_id,),
                        ).fetchone()[0],
                        0,
                    )

        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-ACTIVE-EDIT"
        )
        before = self.batch_state(batch_id, manual_id, [order_id])
        preview = self.edit_preview(batch_id, manual_id, 90).get_json()
        edited = self.post_edit(
            batch_id,
            preview,
            images=(
                BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'),
                "edit.svg",
                "image/svg+xml",
            ),
        )
        self.assertEqual(edited.status_code, 400)
        self.assertIn("安全的位图", edited.get_json()["error"])
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_every_ordinary_and_public_upload_entry_rejects_active_content(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

        create_manual = self.create_product("P-ACTIVE-ORDINARY-CREATE", "客户A")
        create_order = self.create_order(
            create_manual, "SO-ACTIVE-ORDINARY-CREATE", 10
        )
        self.stock_product(create_manual, 10)
        created = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(create_order)],
                "shipped_quantity": ["1"],
                "images": (BytesIO(svg), "create.svg", "image/svg+xml"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", created.get_data(as_text=True))

        _, _, shipment = self.create_ordinary_shipment_with_token(
            "P-ACTIVE-ORDINARY-UPLOAD"
        )
        admin_upload = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/images",
            data={"images": (BytesIO(svg), "admin.svg", "image/svg+xml")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", admin_upload.get_data(as_text=True))

        public_upload = self.client.post(
            f"/shipment-photos/{shipment['photo_upload_token']}",
            data={"images": (BytesIO(svg), "public.svg", "image/svg+xml")},
            content_type="multipart/form-data",
        )
        self.assertEqual(public_upload.status_code, 200)
        self.assertIn("图片格式不支持", public_upload.get_data(as_text=True))

        plan_manual = self.create_product("P-ACTIVE-PLAN", "客户A")
        plan_order = self.create_order(plan_manual, "SO-ACTIVE-PLAN", 10)
        self.stock_product(plan_manual, 10)
        self.client.post(
            "/admin/orders/shipment-plans",
            data={"order_id": [str(plan_order)], "planned_ship_at": "2026-09-01"},
        )
        with app.get_db() as conn:
            plan_item = conn.execute(
                "SELECT plan_id, id FROM shipment_plan_items WHERE order_id = ?",
                (plan_order,),
            ).fetchone()
        approved = self.client.post(
            f"/admin/shipped-orders/plans/{plan_item['plan_id']}/approve",
            data={
                "shipped_at": "2026-08-30",
                "plan_item_id": [str(plan_item["id"])],
                "shipped_quantity": ["1"],
                "images": (BytesIO(svg), "plan.svg", "image/svg+xml"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("发货图片格式不支持", approved.get_data(as_text=True))

        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM product_order_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_assembly_upload_limits_return_json_413_and_remove_partial_stages(self):
        cases = [
            (
                "request",
                {"MAX_CONTENT_LENGTH": 64},
                [(BytesIO(b"x" * 4096), "request.png", "image/png")],
            ),
            (
                "count",
                {"SHIPMENT_IMAGE_MAX_FILES": 1},
                [
                    (BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
                    (BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
                ],
            ),
            (
                "per-file",
                {"SHIPMENT_IMAGE_MAX_FILE_BYTES": len(VALID_PNG_BYTES) - 1},
                [(BytesIO(VALID_PNG_BYTES), "large.png", "image/png")],
            ),
            (
                "total",
                {"SHIPMENT_IMAGE_MAX_TOTAL_BYTES": len(VALID_PNG_BYTES) * 2 - 1},
                [
                    (BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
                    (BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
                ],
            ),
        ]
        for index, (label, config, images) in enumerate(cases):
            with self.subTest(limit=label):
                manual_id, order_id, preview = self.prepare_image_batch(
                    f"P-LIMIT-{index}"
                )
                data = self.save_data(preview)
                data["images"] = images
                with patch.dict(app.app.config, config):
                    response = self.client.post(
                        "/admin/shipped-orders/assembly/new",
                        data=data,
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, 413)
                self.assertIn("error", response.get_json())
                with app.get_db() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM assembly_shipment_items WHERE manual_id = ?",
                            (manual_id,),
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_ordinary_admin_and_public_upload_limits_return_html_413(self):
        _, _, shipment = self.create_ordinary_shipment_with_token("P-LIMIT-ORDINARY")
        images = [
            (BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
            (BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
        ]
        with patch.dict(app.app.config, {"SHIPMENT_IMAGE_MAX_FILES": 1}):
            admin_response = self.client.post(
                f"/admin/shipped-orders/{shipment['id']}/images",
                data={"images": images},
                content_type="multipart/form-data",
            )
        self.assertEqual(admin_response.status_code, 413)
        self.assertIsNone(admin_response.get_json(silent=True))
        self.assertIn("发货图片", admin_response.get_data(as_text=True))

        public_images = [
            (BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
            (BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
        ]
        with patch.dict(app.app.config, {"SHIPMENT_IMAGE_MAX_FILES": 1}):
            public_response = self.client.post(
                f"/shipment-photos/{shipment['photo_upload_token']}",
                data={"images": public_images},
                content_type="multipart/form-data",
            )
        self.assertEqual(public_response.status_code, 413)
        self.assertIn("发货图片", public_response.get_data(as_text=True))
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM product_order_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_create_persists_safe_image_metadata_and_file_and_enables_inputs(self):
        response = self.client.get("/admin/shipped-orders/create")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('name="images" accept="image/*" multiple', page)
        self.assertNotIn('name="images" accept="image/*" multiple disabled', page)

        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-CREATE")
        data = self.save_data(preview)
        data["images"] = (
            BytesIO(VALID_PNG_BYTES),
            "../unsafe name.png",
            "image/png",
        )
        response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=data,
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        batch_id = response.get_json()["batch_id"]
        with app.get_db() as conn:
            image = conn.execute(
                "SELECT * FROM assembly_shipment_images WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        self.assertIsNotNone(image)
        self.assertEqual(image["original_filename"], "unsafe name.png")
        self.assertEqual(image["content_type"], "image/png")
        self.assertEqual(image["file_size"], len(VALID_PNG_BYTES))
        self.assertEqual(image["filename"], app.secure_filename(image["filename"]))
        self.assertEqual(Path(image["filename"]).suffix, ".png")
        self.assertEqual(
            (app.SHIPMENT_IMAGES_DIR / image["filename"]).read_bytes(),
            VALID_PNG_BYTES,
        )
        edit_html = self.client.get(
            f"/admin/shipped-orders/assembly/{batch_id}/edit"
        ).get_data(as_text=True)
        self.assertIn('name="images" accept="image/*" multiple', edit_html)

    def test_direct_shipment_image_requires_shipped_view_permission(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-IMAGE-DIRECT-PERMISSION",
            images=(BytesIO(VALID_PNG_BYTES), "protected.png", "image/png"),
        )
        with app.get_db() as conn:
            filename = conn.execute(
                "SELECT filename FROM assembly_shipment_images WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()["filename"]
            now = "2026-08-30T13:00:00"
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    can_view_shipped, can_manage_shipped, created_at, updated_at
                ) VALUES ('no-shipped-access', 'unused', 'operator', 1, 0, 0, ?, ?)
                """,
                (now, now),
            )
        with self.client.session_transaction() as session:
            session["admin_username"] = "no-shipped-access"
            session["admin_role"] = "operator"

        history = self.client.get("/admin/shipped-orders")
        direct = self.client.get(f"/shipment-image/{filename}")

        self.assertEqual(history.status_code, 302)
        self.assertEqual(direct.status_code, 302)
        self.assertNotEqual(direct.get_data(), VALID_PNG_BYTES)

    def test_legacy_unsafe_shipment_images_download_but_png_stays_inline(self):
        _, _, shipment = self.create_ordinary_shipment_with_token(
            "P-LEGACY-SHIPMENT-IMAGE"
        )
        fixtures = {
            "safe.png": (VALID_PNG_BYTES, "image/png", True),
            "legacy-active.svg": (
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                "image/svg+xml",
                False,
            ),
            "legacy-valid.webp": (VALID_STATIC_WEBP_BYTES, "image/webp", False),
        }
        with app.get_db() as conn:
            for filename, (payload, content_type, _) in fixtures.items():
                (app.SHIPMENT_IMAGES_DIR / filename).write_bytes(payload)
                conn.execute(
                    """
                    INSERT INTO product_order_shipment_images (
                        shipment_id, filename, original_filename, content_type,
                        file_size, uploaded_at
                    ) VALUES (?, ?, ?, ?, ?, '2026-08-30T12:00:00')
                    """,
                    (shipment["id"], filename, filename, content_type, len(payload)),
                )

        admin_responses = {}
        for filename in fixtures:
            response = self.client.get(f"/shipment-image/{filename}")
            self.addCleanup(response.close)
            admin_responses[filename] = response
        with self.client.session_transaction() as session:
            session.clear()
        public_responses = {}
        for filename in fixtures:
            response = self.client.get(
                f"/shipment-photos/{shipment['photo_upload_token']}/image/{filename}"
            )
            self.addCleanup(response.close)
            public_responses[filename] = response

        for route, responses in (
            ("admin", admin_responses),
            ("public", public_responses),
        ):
            for filename, (payload, _, safe_inline) in fixtures.items():
                with self.subTest(route=route, filename=filename):
                    response = responses[filename]
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_data(), payload)
                    disposition = response.headers.get("Content-Disposition", "").lower()
                    if safe_inline:
                        self.assertNotIn("attachment", disposition)
                        self.assertEqual(response.mimetype, "image/png")
                    else:
                        self.assertIn("attachment", disposition)
                        self.assertEqual(response.mimetype, "application/octet-stream")
                        self.assertEqual(
                            response.headers.get("X-Content-Type-Options"), "nosniff"
                        )
                        self.assertIn(
                            "sandbox",
                            response.headers.get("Content-Security-Policy", ""),
                        )

    def test_invalid_image_rejects_create_without_database_or_file_residue(self):
        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-INVALID")
        data = self.save_data(preview)
        data["images"] = (BytesIO(b"not-image"), "payload.txt", "text/plain")

        response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=data,
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(list(app.SHIPMENT_IMAGES_DIR.iterdir()), [])

    def test_file_finalization_failure_rolls_back_and_removes_only_request_files(self):
        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-FINALIZE")
        unrelated = app.SHIPMENT_IMAGES_DIR / "unrelated.png"
        unrelated.write_bytes(b"keep")
        data = self.save_data(preview)
        data["images"] = [
            (BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
            (BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
        ]
        original_finalize = getattr(
            app, "_finalize_assembly_shipment_image", None
        )
        finalize_calls = 0

        def fail_second(stage_path, final_path):
            nonlocal finalize_calls
            finalize_calls += 1
            if original_finalize is not None:
                original_finalize(stage_path, final_path)
            if finalize_calls == 2:
                raise OSError("injected finalization failure after move")

        with patch.object(
            app,
            "_finalize_assembly_shipment_image",
            side_effect=fail_second,
            create=True,
        ):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=data,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {"unrelated.png"},
        )
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_partial_staging_write_failure_removes_request_temp_file(self):
        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-STAGE")
        unrelated = app.SHIPMENT_IMAGES_DIR / "unrelated.png"
        unrelated.write_bytes(b"keep")
        data = self.save_data(preview)
        data["images"] = (
            BytesIO(VALID_PNG_BYTES),
            "partial.png",
            "image/png",
        )

        def partial_then_fail(file_storage, destination, max_file_bytes, remaining_total_bytes):
            Path(destination).write_bytes(b"partial-stage")
            raise OSError("injected partial staging failure")

        with patch.object(
            app, "_copy_shipment_image_stream", side_effect=partial_then_fail
        ):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=data,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {"unrelated.png"},
        )

    def test_database_failure_after_image_write_rolls_back_and_compensates_file(self):
        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-DB-FAIL")
        unrelated = app.SHIPMENT_IMAGES_DIR / "unrelated.png"
        unrelated.write_bytes(b"keep")
        data = self.save_data(preview)
        data["images"] = (BytesIO(VALID_PNG_BYTES), "new.png", "image/png")
        original_save_images = getattr(app, "save_assembly_shipment_images", None)

        def save_then_fail(*args, **kwargs):
            if original_save_images is not None:
                original_save_images(*args, **kwargs)
            raise sqlite3.DatabaseError("injected after image write")

        with patch.object(
            app,
            "save_assembly_shipment_images",
            side_effect=save_then_fail,
            create=True,
        ):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=data,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_images").fetchone()[0],
                0,
            )
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {"unrelated.png"},
        )

    def test_create_reports_success_and_avoids_retry_after_real_commit_exit_error(self):
        manual_id = self.create_product("P-IMAGE-CREATE-AFTER-COMMIT", "客户A")
        self.configure_component(manual_id, quantity_per_set=1)
        preview = self.post_preview(sets=5).get_json()

        def request_data():
            data = self.save_data(preview)
            data["images"] = (
                BytesIO(VALID_PNG_BYTES),
                "create-after-commit.png",
                "image/png",
            )
            return data

        original_get_db = app.get_db
        call_count = 0

        @contextmanager
        def fail_after_route_context_commit():
            nonlocal call_count
            call_count += 1
            call_number = call_count
            with original_get_db() as conn:
                yield conn
            if call_number == 3:
                raise RuntimeError("injected after real create commit")

        with patch.object(app, "get_db", fail_after_route_context_commit):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=request_data(),
                content_type="multipart/form-data",
            )

        if response.status_code >= 400:
            self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=request_data(),
                content_type="multipart/form-data",
            )

        with app.get_db() as conn:
            batches = conn.execute(
                "SELECT * FROM assembly_shipment_batches ORDER BY id"
            ).fetchall()
            items = conn.execute(
                "SELECT * FROM assembly_shipment_items ORDER BY id"
            ).fetchall()
            images = conn.execute(
                "SELECT * FROM assembly_shipment_images ORDER BY id"
            ).fetchall()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(images), 1)
        batch = batches[0]
        item = items[0]
        image = images[0]
        self.assertEqual(response.get_json().get("batch_id"), batch["id"])
        self.assertEqual(item["batch_id"], batch["id"])
        self.assertEqual(item["shipped_quantity"], 5)
        self.assertEqual(item["inventory_deducted_quantity"], 0)
        self.assertEqual(item["inventory_shortage_quantity"], 5)
        self.assertEqual(image["batch_id"], batch["id"])
        image_path = app.SHIPMENT_IMAGES_DIR / image["filename"]
        self.assertTrue(image_path.exists())
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {image["filename"]},
        )
        self.assertEqual(
            image_path.read_bytes(),
            VALID_PNG_BYTES,
        )

    def test_generated_name_collision_never_overwrites_or_deletes_preexisting_file(self):
        manual_id, order_id, preview = self.prepare_image_batch("P-IMAGE-COLLISION")
        collision = app.SHIPMENT_IMAGES_DIR / "collision.png"
        collision.write_bytes(b"preexisting")
        data = self.save_data(preview)
        data["images"] = (
            BytesIO(VALID_PNG_BYTES),
            "request.png",
            "image/png",
        )

        with patch.object(
            app,
            "stored_assembly_shipment_image_filename",
            return_value="collision.png",
        ):
            response = self.client.post(
                "/admin/shipped-orders/assembly/new",
                data=data,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(collision.read_bytes(), b"preexisting")
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {"collision.png"},
        )
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_batches").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM assembly_shipment_images").fetchone()[0],
                0,
            )

    def test_edit_adds_image_and_failed_next_edit_preserves_old_files_and_state(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-IMAGE-EDIT",
            quantity=80,
            order_quantity=80,
            stock_quantity=100,
            images=(BytesIO(VALID_PNG_BYTES), "old.png", "image/png"),
        )
        preview = self.edit_preview(batch_id, manual_id, 80).get_json()
        success = self.post_edit(
            batch_id,
            preview,
            images=(BytesIO(VALID_PNG_BYTES), "added.png", "image/png"),
        )
        self.assertEqual(success.status_code, 201)
        before = self.batch_state(batch_id, manual_id, [order_id])
        before_files = {
            path.name: path.read_bytes() for path in app.SHIPMENT_IMAGES_DIR.iterdir()
        }
        self.assertEqual(len(before["images"]), 2)

        next_preview = self.edit_preview(batch_id, manual_id, 70).get_json()
        original_save_images = app.save_assembly_shipment_images

        def save_then_fail(*args, **kwargs):
            original_save_images(*args, **kwargs)
            raise sqlite3.DatabaseError("injected edit image database failure")

        with patch.object(
            app, "save_assembly_shipment_images", side_effect=save_then_fail
        ):
            failed = self.post_edit(
                batch_id,
                next_preview,
                images=(BytesIO(VALID_PNG_BYTES), "failed.png", "image/png"),
            )

        self.assertEqual(failed.status_code, 500)
        self.assertEqual(self.batch_state(batch_id, manual_id, [order_id]), before)
        self.assertEqual(
            {path.name: path.read_bytes() for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            before_files,
        )

    def test_edit_reports_success_and_avoids_retry_after_real_commit_exit_error(self):
        batch_id, manual_id, order_id = self.create_single_batch(
            drawing_no="P-IMAGE-EDIT-AFTER-COMMIT",
            quantity=80,
            order_quantity=80,
            stock_quantity=100,
            images=(
                BytesIO(VALID_PNG_BYTES),
                "old-committed.png",
                "image/png",
            ),
        )
        preview = self.edit_preview(batch_id, manual_id, 70).get_json()
        original_get_db = app.get_db
        call_count = 0

        @contextmanager
        def fail_after_route_context_commit():
            nonlocal call_count
            call_count += 1
            call_number = call_count
            with original_get_db() as conn:
                yield conn
            if call_number == 3:
                raise sqlite3.DatabaseError("injected after real edit commit")

        def submit_edit():
            return self.post_edit(
                batch_id,
                preview,
                images=(
                    BytesIO(VALID_PNG_BYTES),
                    "edit-after-commit.png",
                    "image/png",
                ),
            )

        with patch.object(app, "get_db", fail_after_route_context_commit):
            response = submit_edit()

        if response.status_code >= 400:
            submit_edit()

        with app.get_db() as conn:
            batch = conn.execute(
                "SELECT * FROM assembly_shipment_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            item = conn.execute(
                "SELECT * FROM assembly_shipment_items WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            images = conn.execute(
                "SELECT * FROM assembly_shipment_images WHERE batch_id = ? ORDER BY id",
                (batch_id,),
            ).fetchall()
            balance = app.inventory_total_for_manual(conn, manual_id)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json().get("batch_id"), batch_id)
        self.assertEqual(batch["shipped_at"], "2026-08-31")
        self.assertEqual(item["shipped_quantity"], 70)
        self.assertEqual(balance, 30)
        self.assertEqual(len(images), 2)
        expected_contents = {
            "old-committed.png": VALID_PNG_BYTES,
            "edit-after-commit.png": VALID_PNG_BYTES,
        }
        self.assertEqual(
            {image["original_filename"] for image in images},
            set(expected_contents),
        )
        for image in images:
            image_path = app.SHIPMENT_IMAGES_DIR / image["filename"]
            self.assertTrue(image_path.exists(), image["original_filename"])
            self.assertEqual(
                image_path.read_bytes(),
                expected_contents[image["original_filename"]],
            )
        self.assertEqual(
            {path.name for path in app.SHIPMENT_IMAGES_DIR.iterdir()},
            {image["filename"] for image in images},
        )

    def test_delete_unlinks_only_safe_owned_files_after_commit(self):
        first_batch, first_manual, first_order = self.create_single_batch(
            drawing_no="P-IMAGE-DELETE-1",
            images=(BytesIO(VALID_PNG_BYTES), "first.png", "image/png"),
        )
        second_batch, second_manual, second_order = self.create_single_batch(
            drawing_no="P-IMAGE-DELETE-2",
            images=(BytesIO(VALID_PNG_BYTES), "second.png", "image/png"),
        )
        unrelated = app.SHIPMENT_IMAGES_DIR / "unrelated.png"
        unrelated.write_bytes(b"keep")
        with app.get_db() as conn:
            first_image = conn.execute(
                "SELECT filename FROM assembly_shipment_images WHERE batch_id = ?",
                (first_batch,),
            ).fetchone()
            second_image = conn.execute(
                "SELECT filename FROM assembly_shipment_images WHERE batch_id = ?",
                (second_batch,),
            ).fetchone()
            self.assertIsNotNone(first_image)
            self.assertIsNotNone(second_image)
            first_filename = first_image["filename"]
            second_filename = second_image["filename"]
            conn.execute(
                """
                INSERT INTO assembly_shipment_images (
                    batch_id, filename, original_filename, content_type,
                    file_size, uploaded_at, uploaded_ip, uploaded_user_agent
                ) VALUES (?, '../unrelated.png', 'unrelated.png', 'image/png', 4, 'now', '', '')
                """,
                (first_batch,),
            )

        response = self.client.post(
            f"/admin/shipped-orders/assembly/{first_batch}/delete"
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse((app.SHIPMENT_IMAGES_DIR / first_filename).exists())
        self.assertTrue((app.SHIPMENT_IMAGES_DIR / second_filename).exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_images WHERE batch_id = ?",
                    (first_batch,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_images WHERE batch_id = ?",
                    (second_batch,),
                ).fetchone()[0],
                1,
            )

    def test_delete_preserves_safe_files_outside_batch_ownership_or_still_referenced(self):
        first_batch, first_manual, first_order = self.create_single_batch(
            drawing_no="P-IMAGE-OWNERSHIP-1",
            images=(BytesIO(VALID_PNG_BYTES), "first-owned.png", "image/png"),
        )
        second_batch, second_manual, second_order = self.create_single_batch(
            drawing_no="P-IMAGE-OWNERSHIP-2",
            images=(BytesIO(VALID_PNG_BYTES), "second-owned.png", "image/png"),
        )
        ordinary_manual = self.create_product("P-IMAGE-OWNERSHIP-ORDINARY", "客户B")
        ordinary_order = self.create_order(
            ordinary_manual, "SO-IMAGE-OWNERSHIP-ORDINARY", 1, customer="客户B"
        )
        ordinary_shipment = self.create_shipment(ordinary_order, 1)

        safe_unrelated = "safe-unrelated.png"
        shared_with_assembly = app.stored_assembly_shipment_image_filename(
            first_batch, "assembly-shared.png", ".png"
        )
        shared_with_ordinary = app.stored_assembly_shipment_image_filename(
            first_batch, "ordinary-shared.png", ".png"
        )
        for filename, content in (
            (safe_unrelated, b"safe-unrelated"),
            (shared_with_assembly, b"assembly-shared"),
            (shared_with_ordinary, b"ordinary-shared"),
        ):
            (app.SHIPMENT_IMAGES_DIR / filename).write_bytes(content)

        with app.get_db() as conn:
            first_filename = conn.execute(
                "SELECT filename FROM assembly_shipment_images WHERE batch_id = ?",
                (first_batch,),
            ).fetchone()["filename"]
            second_filename = conn.execute(
                "SELECT filename FROM assembly_shipment_images WHERE batch_id = ?",
                (second_batch,),
            ).fetchone()["filename"]
            for filename in (
                second_filename,
                safe_unrelated,
                shared_with_assembly,
                shared_with_ordinary,
            ):
                conn.execute(
                    """
                    INSERT INTO assembly_shipment_images (
                        batch_id, filename, original_filename, content_type,
                        file_size, uploaded_at, uploaded_ip, uploaded_user_agent
                    ) VALUES (?, ?, ?, 'image/png', 1, 'now', '', '')
                    """,
                    (first_batch, filename, filename),
                )
            conn.execute(
                """
                INSERT INTO assembly_shipment_images (
                    batch_id, filename, original_filename, content_type,
                    file_size, uploaded_at, uploaded_ip, uploaded_user_agent
                ) VALUES (?, ?, ?, 'image/png', 1, 'now', '', '')
                """,
                (second_batch, shared_with_assembly, shared_with_assembly),
            )
            conn.execute(
                """
                INSERT INTO product_order_shipment_images (
                    shipment_id, filename, original_filename, content_type,
                    file_size, uploaded_at, uploaded_ip, uploaded_user_agent
                ) VALUES (?, ?, ?, 'image/png', 1, 'now', '', '')
                """,
                (ordinary_shipment, shared_with_ordinary, shared_with_ordinary),
            )

        response = self.client.post(
            f"/admin/shipped-orders/assembly/{first_batch}/delete"
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse((app.SHIPMENT_IMAGES_DIR / first_filename).exists())
        expected_files = {
            second_filename: VALID_PNG_BYTES,
            safe_unrelated: b"safe-unrelated",
            shared_with_assembly: b"assembly-shared",
            shared_with_ordinary: b"ordinary-shared",
        }
        self.assertEqual(
            {
                filename: (app.SHIPMENT_IMAGES_DIR / filename).read_bytes()
                for filename in expected_files
                if (app.SHIPMENT_IMAGES_DIR / filename).exists()
            },
            expected_files,
        )
        with app.get_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_images WHERE batch_id = ?",
                    (first_batch,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM assembly_shipment_images WHERE batch_id = ?",
                    (second_batch,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM product_order_shipment_images WHERE shipment_id = ?",
                    (ordinary_shipment,),
                ).fetchone()[0],
                1,
            )

    def test_ordinary_shipment_image_edit_delete_path_remains_unchanged(self):
        manual_id = self.create_product("P-ORDINARY-IMAGE", "客户A")
        order_id = self.create_order(manual_id, "SO-ORDINARY-IMAGE", 10)
        self.stock_product(manual_id, 10)
        create_response = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(order_id)],
                "shipped_quantity": ["3"],
                "images": (
                    BytesIO(VALID_PNG_BYTES),
                    "ordinary.png",
                    "image/png",
                ),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(create_response.status_code, 302)
        with app.get_db() as conn:
            shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?", (order_id,)
            ).fetchone()
            image = conn.execute(
                "SELECT * FROM product_order_shipment_images WHERE shipment_id = ?",
                (shipment["id"],),
            ).fetchone()
        self.assertIsNotNone(image)
        ordinary_path = app.SHIPMENT_IMAGES_DIR / image["filename"]
        self.assertEqual(ordinary_path.read_bytes(), VALID_PNG_BYTES)

        edit_response = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/edit",
            data={
                "shipped_at": "2026-08-31",
                "shipped_quantity": "4",
                "logistics_no": "ordinary-edit",
            },
        )
        delete_response = self.client.post(
            f"/admin/shipped-orders/{shipment['id']}/delete"
        )

        self.assertEqual(edit_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(ordinary_path.exists())
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, manual_id), 10)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM product_order_shipment_images"
                ).fetchone()[0],
                0,
            )

    def test_public_shipment_photo_token_keeps_object_scoped_image_access(self):
        first_manual = self.create_product("P-PUBLIC-PHOTO-1", "客户A")
        first_order = self.create_order(first_manual, "SO-PUBLIC-PHOTO-1", 1)
        self.stock_product(first_manual, 1)
        first_create = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(first_order)],
                "shipped_quantity": ["1"],
                "images": (
                    BytesIO(VALID_PNG_BYTES),
                    "public-object.png",
                    "image/png",
                ),
            },
            content_type="multipart/form-data",
        )
        second_manual = self.create_product("P-PUBLIC-PHOTO-2", "客户B")
        second_order = self.create_order(
            second_manual, "SO-PUBLIC-PHOTO-2", 1, customer="客户B"
        )
        self.stock_product(second_manual, 1)
        second_create = self.client.post(
            "/admin/shipped-orders/new",
            data={
                "shipped_at": "2026-08-30",
                "order_id": [str(second_order)],
                "shipped_quantity": ["1"],
            },
        )
        self.assertEqual(first_create.status_code, 302)
        self.assertEqual(second_create.status_code, 302)

        with app.get_db() as conn:
            first_shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (first_order,),
            ).fetchone()
            second_shipment = conn.execute(
                "SELECT * FROM product_order_shipments WHERE order_id = ?",
                (second_order,),
            ).fetchone()
            image = conn.execute(
                "SELECT * FROM product_order_shipment_images WHERE shipment_id = ?",
                (first_shipment["id"],),
            ).fetchone()
        self.assertTrue(first_shipment["photo_upload_token"])
        self.assertTrue(second_shipment["photo_upload_token"])
        self.assertIsNotNone(image)

        with self.client.session_transaction() as session:
            session.clear()
        owning_token = self.client.get(
            f"/shipment-photos/{first_shipment['photo_upload_token']}/image/{image['filename']}"
        )
        other_valid_token = self.client.get(
            f"/shipment-photos/{second_shipment['photo_upload_token']}/image/{image['filename']}"
        )
        direct_admin_path = self.client.get(f"/shipment-image/{image['filename']}")

        self.assertEqual(owning_token.status_code, 200)
        self.assertEqual(owning_token.get_data(), VALID_PNG_BYTES)
        self.assertEqual(other_valid_token.status_code, 404)
        self.assertEqual(direct_admin_path.status_code, 302)


if __name__ == "__main__":
    unittest.main()
