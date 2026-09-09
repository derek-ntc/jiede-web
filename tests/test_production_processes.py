import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
import production_processes


class ProductionProcessPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.init_db()

        with app.get_db() as conn:
            self.manual_id = self._insert_manual(conn, "P-100", "产品甲")

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    @staticmethod
    def _insert_manual(conn, drawing_no, product_name):
        now = "2026-09-09T09:00:00"
        return conn.execute(
            """
            INSERT INTO manuals (
                drawing_no, product_name, customer, supplier, model, category,
                version, remark, filename, original_filename, created_at,
                updated_at, unit_price_minor, currency
            ) VALUES (?, ?, '客户A', '规格-A', '', '', '', '', '', '', ?, ?, 0, 'CNY')
            """,
            (drawing_no, product_name, now, now),
        ).lastrowid

    @staticmethod
    def _insert_followup(
        conn,
        *,
        followup_id=None,
        manual_id=None,
        laser="",
        bending="",
        welding="",
        created_by="",
    ):
        now = "2026-09-09T09:00:00"
        return conn.execute(
            """
            INSERT INTO production_followups (
                id, batch_no, customer, manual_id, ordered_at, drawing_no,
                product_name, laser_completed_at, bending_completed_at,
                welding_completed_at, created_by, created_at, updated_at
            ) VALUES (?, '', '客户A', ?, '2026-09-09', 'P-100', '产品甲',
                      ?, ?, ?, ?, ?, ?)
            """,
            (
                followup_id,
                manual_id,
                laser,
                bending,
                welding,
                created_by,
                now,
                now,
            ),
        ).lastrowid

    def test_startup_schema_and_legacy_backfill_are_idempotent(self):
        with app.get_db() as conn:
            conn.execute(
                "ALTER TABLE production_followups ADD COLUMN laser_completed_by TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "ALTER TABLE production_followups ADD COLUMN welding_completed_by TEXT NOT NULL DEFAULT ''"
            )
            followup_id = self._insert_followup(
                conn,
                manual_id=self.manual_id,
                laser="2026-09-09T10:00:00",
                welding="2026-09-09T12:00:00",
                created_by="legacy-owner",
            )
            conn.execute(
                """
                UPDATE production_followups
                SET laser_completed_by = 'laser-worker',
                    welding_completed_by = 'welding-worker'
                WHERE id = ?
                """,
                (followup_id,),
            )

        app.init_db()
        app.init_db()

        with app.get_db() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            steps = conn.execute(
                """
                SELECT name, sort_order, completed_at, completed_by
                FROM production_followup_process_steps
                WHERE followup_id = ?
                ORDER BY sort_order, id
                """,
                (followup_id,),
            ).fetchall()

        self.assertTrue(
            {
                "manual_process_configs",
                "manual_process_steps",
                "production_followup_process_steps",
            }.issubset(tables)
        )
        self.assertEqual(
            [tuple(row) for row in steps],
            [
                ("激光", 0, "2026-09-09T10:00:00", "laser-worker"),
                ("折弯", 1, "", ""),
                ("焊接", 2, "2026-09-09T12:00:00", "welding-worker"),
            ],
        )

    def test_no_config_uses_defaults_but_saved_empty_config_stays_empty(self):
        with app.get_db() as conn:
            self.assertEqual(
                [step["name"] for step in production_processes.load_manual_process_template(conn, self.manual_id)],
                ["激光", "折弯", "焊接"],
            )

            saved = production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                [],
                now="2026-09-09T10:00:00",
            )
            config = conn.execute(
                "SELECT manual_id FROM manual_process_configs WHERE manual_id = ?",
                (self.manual_id,),
            ).fetchone()

            self.assertEqual(saved, [])
            self.assertIsNotNone(config)
            self.assertEqual(
                production_processes.load_manual_process_template(conn, self.manual_id),
                [],
            )

    def test_template_save_normalizes_names_and_rejects_casefold_duplicates_atomically(self):
        with app.get_db() as conn:
            saved = production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                ["  Cutting  ", "折弯", "包装"],
                now="2026-09-09T10:00:00",
            )
            self.assertEqual(
                [(step["name"], step["sort_order"]) for step in saved],
                [("Cutting", 0), ("折弯", 1), ("包装", 2)],
            )

            with self.assertRaisesRegex(ValueError, "不能重复"):
                production_processes.save_manual_process_template(
                    conn,
                    self.manual_id,
                    ["Cut", "cut"],
                    now="2026-09-09T11:00:00",
                )

            self.assertEqual(
                [step["name"] for step in production_processes.load_manual_process_template(conn, self.manual_id)],
                ["Cutting", "折弯", "包装"],
            )

            with self.assertRaisesRegex(ValueError, "100"):
                production_processes.save_manual_process_template(
                    conn,
                    self.manual_id,
                    ["工" * 101],
                    now="2026-09-09T11:00:00",
                )
            with self.assertRaisesRegex(ValueError, "不能为空"):
                production_processes.save_manual_process_template(
                    conn,
                    self.manual_id,
                    ["   "],
                    now="2026-09-09T11:00:00",
                )

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO manual_process_steps (
                        manual_id, name, sort_order, created_at, updated_at
                    ) VALUES (?, ?, 99, '', '')
                    """,
                    (self.manual_id, "工" * 101),
                )

    def test_template_save_remains_rollbackable_by_callers_connection_context(self):
        with self.assertRaisesRegex(RuntimeError, "caller failure"):
            with app.get_db() as conn:
                production_processes.save_manual_process_template(
                    conn,
                    self.manual_id,
                    ["下料", "包装"],
                    now="2026-09-09T10:00:00",
                )
                raise RuntimeError("caller failure")

        with app.get_db() as conn:
            config = conn.execute(
                "SELECT manual_id FROM manual_process_configs WHERE manual_id = ?",
                (self.manual_id,),
            ).fetchone()
            template = production_processes.load_manual_process_template(
                conn, self.manual_id
            )

        self.assertIsNone(config)
        self.assertEqual(
            [step["name"] for step in template],
            ["激光", "折弯", "焊接"],
        )

    def test_followup_snapshot_copies_explicit_template_and_does_not_follow_later_edits(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                ["下料", "钻孔"],
                now="2026-09-09T10:00:00",
            )
            followup_id = self._insert_followup(conn, manual_id=self.manual_id)
            snapshot = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                self.manual_id,
                now="2026-09-09T10:05:00",
            )
            production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                ["喷涂"],
                now="2026-09-09T11:00:00",
            )
            historical = production_processes.load_followup_process_card(
                conn, followup_id
            )

        self.assertEqual(
            [(step["name"], step["sort_order"]) for step in snapshot],
            [("下料", 0), ("钻孔", 1)],
        )
        self.assertEqual(
            [step["name"] for step in historical],
            ["下料", "钻孔"],
        )

    def test_followup_snapshot_rejects_a_template_from_an_unrelated_product(self):
        with app.get_db() as conn:
            other_manual_id = self._insert_manual(conn, "P-200", "产品乙")
            production_processes.save_manual_process_template(
                conn,
                other_manual_id,
                ["不应复制"],
                now="2026-09-09T10:00:00",
            )
            followup_id = self._insert_followup(conn, manual_id=self.manual_id)

            with self.assertRaisesRegex(ValueError, "关联产品"):
                production_processes.create_followup_process_snapshot(
                    conn,
                    followup_id,
                    other_manual_id,
                    now="2026-09-09T10:05:00",
                )

            followup = conn.execute(
                """
                SELECT process_snapshot_created
                FROM production_followups
                WHERE id = ?
                """,
                (followup_id,),
            ).fetchone()
            card = production_processes.load_followup_process_card(
                conn, followup_id
            )

        self.assertEqual(followup["process_snapshot_created"], 0)
        self.assertEqual(card, [])

    def test_followup_snapshot_distinguishes_empty_config_from_missing_product_config(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                [],
                now="2026-09-09T10:00:00",
            )
            empty_followup_id = self._insert_followup(conn, manual_id=self.manual_id)
            default_followup_id = self._insert_followup(conn, manual_id=None)

            explicit_empty = production_processes.create_followup_process_snapshot(
                conn,
                empty_followup_id,
                self.manual_id,
                now="2026-09-09T10:05:00",
            )
            defaults = production_processes.create_followup_process_snapshot(
                conn,
                default_followup_id,
                None,
                now="2026-09-09T10:05:00",
            )

        app.init_db()
        with app.get_db() as conn:
            explicit_empty_after_restart = (
                production_processes.load_followup_process_card(
                    conn, empty_followup_id
                )
            )

        self.assertEqual(explicit_empty, [])
        self.assertEqual(explicit_empty_after_restart, [])
        self.assertEqual(
            [step["name"] for step in defaults],
            ["激光", "折弯", "焊接"],
        )

    def test_completion_and_revert_are_limited_to_the_process_boundary(self):
        with app.get_db() as conn:
            followup_id = self._insert_followup(conn, manual_id=None)
            card = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                now="2026-09-09T10:00:00",
            )

            with self.assertRaisesRegex(ValueError, "第一项未完成"):
                production_processes.complete_followup_process_step(
                    conn,
                    followup_id,
                    card[1]["id"],
                    "operator-a",
                    completed_at="2026-09-09T10:10:00+08:00",
                )
            with self.assertRaisesRegex(ValueError, "操作人"):
                production_processes.complete_followup_process_step(
                    conn,
                    followup_id,
                    card[0]["id"],
                    "  ",
                    completed_at="2026-09-09T10:10:00+08:00",
                )

            after_laser = production_processes.complete_followup_process_step(
                conn,
                followup_id,
                card[0]["id"],
                "operator-a",
                completed_at="2026-09-09T10:11:00+08:00",
            )
            after_bending = production_processes.complete_followup_process_step(
                conn,
                followup_id,
                card[1]["id"],
                "operator-b",
                completed_at="2026-09-09T10:12:00+08:00",
            )

            with self.assertRaisesRegex(ValueError, "最后一项已完成"):
                production_processes.revert_followup_process_step(
                    conn,
                    followup_id,
                    card[0]["id"],
                    now="2026-09-09T10:13:00+08:00",
                )

            reverted = production_processes.revert_followup_process_step(
                conn,
                followup_id,
                card[1]["id"],
                now="2026-09-09T10:14:00+08:00",
            )
            legacy = conn.execute(
                """
                SELECT laser_completed_at, bending_completed_at,
                       welding_completed_at
                FROM production_followups
                WHERE id = ?
                """,
                (followup_id,),
            ).fetchone()

        self.assertEqual(
            [(step["completed_at"], step["completed_by"]) for step in after_laser],
            [
                ("2026-09-09T10:11:00+08:00", "operator-a"),
                ("", ""),
                ("", ""),
            ],
        )
        self.assertEqual(after_bending[1]["completed_by"], "operator-b")
        self.assertEqual(
            [(step["completed_at"], step["completed_by"]) for step in reverted],
            [
                ("2026-09-09T10:11:00+08:00", "operator-a"),
                ("", ""),
                ("", ""),
            ],
        )
        self.assertEqual(
            tuple(legacy),
            ("2026-09-09T10:11:00+08:00", "", ""),
        )

    def test_completion_remains_rollbackable_by_callers_connection_context(self):
        with app.get_db() as conn:
            followup_id = self._insert_followup(conn, manual_id=None)
            card = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                now="2026-09-09T10:00:00",
            )
            first_step_id = card[0]["id"]

        with self.assertRaisesRegex(RuntimeError, "caller failure"):
            with app.get_db() as conn:
                production_processes.complete_followup_process_step(
                    conn,
                    followup_id,
                    first_step_id,
                    "operator-a",
                    completed_at="2026-09-09T10:10:00+08:00",
                )
                raise RuntimeError("caller failure")

        with app.get_db() as conn:
            card = production_processes.load_followup_process_card(
                conn, followup_id
            )
            legacy = conn.execute(
                "SELECT laser_completed_at FROM production_followups WHERE id = ?",
                (followup_id,),
            ).fetchone()

        self.assertEqual(card[0]["completed_at"], "")
        self.assertEqual(card[0]["completed_by"], "")
        self.assertEqual(legacy["laser_completed_at"], "")

    def test_add_delete_and_move_only_change_the_uncompleted_tail(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                ["切割", "检验", "包装"],
                now="2026-09-09T10:00:00",
            )
            followup_id = self._insert_followup(conn, manual_id=self.manual_id)
            card = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                now="2026-09-09T10:00:00",
            )
            production_processes.complete_followup_process_step(
                conn,
                followup_id,
                card[0]["id"],
                "operator-a",
                completed_at="2026-09-09T10:10:00+08:00",
            )
            added = production_processes.add_followup_process_step(
                conn,
                followup_id,
                "  入库  ",
                now="2026-09-09T10:11:00+08:00",
            )
            self.assertEqual(
                [(step["name"], step["sort_order"]) for step in added],
                [("切割", 0), ("检验", 1), ("包装", 2), ("入库", 3)],
            )

            moved = production_processes.move_followup_process_step(
                conn,
                followup_id,
                card[2]["id"],
                "up",
                now="2026-09-09T10:12:00+08:00",
            )
            self.assertEqual(
                [step["name"] for step in moved],
                ["切割", "包装", "检验", "入库"],
            )

            with self.assertRaisesRegex(ValueError, "已完成工艺"):
                production_processes.move_followup_process_step(
                    conn,
                    followup_id,
                    card[0]["id"],
                    "down",
                    now="2026-09-09T10:13:00+08:00",
                )
            with self.assertRaisesRegex(ValueError, "已完成工艺"):
                production_processes.move_followup_process_step(
                    conn,
                    followup_id,
                    card[2]["id"],
                    "up",
                    now="2026-09-09T10:13:00+08:00",
                )
            with self.assertRaisesRegex(ValueError, "先撤回"):
                production_processes.delete_followup_process_step(
                    conn,
                    followup_id,
                    card[0]["id"],
                    now="2026-09-09T10:14:00+08:00",
                )

            deleted = production_processes.delete_followup_process_step(
                conn,
                followup_id,
                card[1]["id"],
                now="2026-09-09T10:15:00+08:00",
            )

        self.assertEqual(
            [(step["name"], step["sort_order"]) for step in deleted],
            [("切割", 0), ("包装", 1), ("入库", 2)],
        )

    def test_all_recognized_processes_project_completion_and_revert_to_legacy_columns(self):
        with app.get_db() as conn:
            followup_id = self._insert_followup(conn, manual_id=None)
            card = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                now="2026-09-09T10:00:00",
            )
            timestamps = [
                "2026-09-09T10:10:00+08:00",
                "2026-09-09T10:20:00+08:00",
                "2026-09-09T10:30:00+08:00",
            ]
            for step, completed_at in zip(card, timestamps):
                production_processes.complete_followup_process_step(
                    conn,
                    followup_id,
                    step["id"],
                    "operator-a",
                    completed_at=completed_at,
                )
            completed_legacy = conn.execute(
                """
                SELECT laser_completed_at, bending_completed_at,
                       welding_completed_at
                FROM production_followups WHERE id = ?
                """,
                (followup_id,),
            ).fetchone()

            for step in reversed(card):
                production_processes.revert_followup_process_step(
                    conn,
                    followup_id,
                    step["id"],
                    now="2026-09-09T11:00:00+08:00",
                )
            reverted_legacy = conn.execute(
                """
                SELECT laser_completed_at, bending_completed_at,
                       welding_completed_at
                FROM production_followups WHERE id = ?
                """,
                (followup_id,),
            ).fetchone()

        self.assertEqual(tuple(completed_legacy), tuple(timestamps))
        self.assertEqual(tuple(reverted_legacy), ("", "", ""))

    def test_legacy_projection_failure_rolls_back_step_completion(self):
        with app.get_db() as conn:
            followup_id = self._insert_followup(conn, manual_id=None)
            card = production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                now="2026-09-09T10:00:00",
            )
            conn.execute(
                """
                CREATE TRIGGER reject_legacy_laser_projection
                BEFORE UPDATE OF laser_completed_at ON production_followups
                BEGIN
                    SELECT RAISE(ABORT, '故障注入');
                END
                """
            )

            with self.assertRaisesRegex(sqlite3.DatabaseError, "故障注入"):
                production_processes.complete_followup_process_step(
                    conn,
                    followup_id,
                    card[0]["id"],
                    "operator-a",
                    completed_at="2026-09-09T10:10:00+08:00",
                )

            unchanged = production_processes.load_followup_process_card(
                conn, followup_id
            )

        self.assertEqual(unchanged[0]["completed_at"], "")
        self.assertEqual(unchanged[0]["completed_by"], "")

    def test_deleting_a_product_removes_only_its_template_not_historical_snapshots(self):
        with app.get_db() as conn:
            production_processes.save_manual_process_template(
                conn,
                self.manual_id,
                ["下料", "包装"],
                now="2026-09-09T10:00:00",
            )
            followup_id = self._insert_followup(conn, manual_id=self.manual_id)
            production_processes.create_followup_process_snapshot(
                conn,
                followup_id,
                self.manual_id,
                now="2026-09-09T10:05:00",
            )

        app.delete_manuals([self.manual_id])

        with app.get_db() as conn:
            config_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_process_configs"
            ).fetchone()["c"]
            template_step_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_process_steps"
            ).fetchone()["c"]
            historical = production_processes.load_followup_process_card(
                conn, followup_id
            )

        self.assertEqual(config_count, 0)
        self.assertEqual(template_step_count, 0)
        self.assertEqual([step["name"] for step in historical], ["下料", "包装"])


if __name__ == "__main__":
    unittest.main()
