"""Persistence and state rules for configurable production processes."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import uuid


DEFAULT_PROCESS_NAMES = ("激光", "折弯", "焊接")
LEGACY_PROCESS_COLUMNS = {
    "激光": "laser_completed_at",
    "折弯": "bending_completed_at",
    "焊接": "welding_completed_at",
}
MAX_PROCESS_NAME_LENGTH = 100


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _beijing_now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


@contextmanager
def _savepoint(conn, prefix):
    name = f"{prefix}_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {name}")


def _table_columns(conn, table_name):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def ensure_production_process_tables(conn):
    """Create process tables and idempotently snapshot legacy follow-ups."""
    followup_columns = _table_columns(conn, "production_followups")
    if "process_snapshot_created" not in followup_columns:
        conn.execute(
            """
            ALTER TABLE production_followups
            ADD COLUMN process_snapshot_created INTEGER NOT NULL DEFAULT 0
            CHECK (process_snapshot_created IN (0, 1))
            """
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_process_configs (
            manual_id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (manual_id) REFERENCES manuals(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS manual_process_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manual_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                name = TRIM(name)
                AND LENGTH(name) BETWEEN 1 AND {MAX_PROCESS_NAME_LENGTH}
            ),
            FOREIGN KEY (manual_id) REFERENCES manual_process_configs(manual_id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_process_steps_name
        ON manual_process_steps (manual_id, name COLLATE NOCASE)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_manual_process_steps_order
        ON manual_process_steps (manual_id, sort_order, id)
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS production_followup_process_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            followup_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            completed_at TEXT NOT NULL DEFAULT '',
            completed_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                name = TRIM(name)
                AND LENGTH(name) BETWEEN 1 AND {MAX_PROCESS_NAME_LENGTH}
            ),
            FOREIGN KEY (followup_id) REFERENCES production_followups(id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_production_followup_process_steps_order
        ON production_followup_process_steps (followup_id, sort_order, id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_production_followup_process_steps_name
        ON production_followup_process_steps (followup_id, name COLLATE NOCASE)
        """
    )

    conn.execute(
        """
        UPDATE production_followups
        SET process_snapshot_created = 1
        WHERE process_snapshot_created = 0
          AND EXISTS (
              SELECT 1
              FROM production_followup_process_steps
              WHERE production_followup_process_steps.followup_id = production_followups.id
          )
        """
    )
    legacy_rows = conn.execute(
        """
        SELECT *
        FROM production_followups
        WHERE process_snapshot_created = 0
          AND NOT EXISTS (
              SELECT 1
              FROM production_followup_process_steps
              WHERE production_followup_process_steps.followup_id = production_followups.id
          )
        ORDER BY id
        """
    ).fetchall()
    for followup in legacy_rows:
        created_at = followup["created_at"] or _now()
        updated_at = followup["updated_at"] or created_at
        for sort_order, process_name in enumerate(DEFAULT_PROCESS_NAMES):
            legacy_column = LEGACY_PROCESS_COLUMNS[process_name]
            completed_at = followup[legacy_column] or ""
            operator_column = legacy_column.replace("_at", "_by")
            completed_by = (
                followup[operator_column] or ""
                if operator_column in followup.keys() and completed_at
                else ""
            )
            conn.execute(
                """
                INSERT INTO production_followup_process_steps (
                    followup_id, name, sort_order, completed_at, completed_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    followup["id"],
                    process_name,
                    sort_order,
                    completed_at,
                    completed_by,
                    created_at,
                    updated_at,
                ),
            )
        conn.execute(
            """
            UPDATE production_followups
            SET process_snapshot_created = 1
            WHERE id = ?
            """,
            (followup["id"],),
        )


def _normalize_process_names(names):
    if not isinstance(names, (list, tuple)):
        raise ValueError("生产工艺列表格式无效")
    normalized = []
    seen = set()
    for raw_name in names:
        if not isinstance(raw_name, str):
            raise ValueError("生产工艺名称格式无效")
        name = raw_name.strip()
        if not name:
            raise ValueError("生产工艺名称不能为空")
        if len(name) > MAX_PROCESS_NAME_LENGTH:
            raise ValueError("生产工艺名称不能超过 100 个字符")
        folded = name.casefold()
        if folded in seen:
            raise ValueError("同一产品的生产工艺名称不能重复")
        seen.add(folded)
        normalized.append(name)
    return normalized


def load_manual_process_template(conn, manual_id):
    config = conn.execute(
        "SELECT manual_id FROM manual_process_configs WHERE manual_id = ?",
        (manual_id,),
    ).fetchone()
    if config is None:
        return [
            {
                "id": None,
                "manual_id": manual_id,
                "name": name,
                "sort_order": sort_order,
                "created_at": "",
                "updated_at": "",
            }
            for sort_order, name in enumerate(DEFAULT_PROCESS_NAMES)
        ]
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM manual_process_steps
            WHERE manual_id = ?
            ORDER BY sort_order, id
            """,
            (manual_id,),
        ).fetchall()
    ]


def save_manual_process_template(conn, manual_id, names, *, now=None):
    normalized = _normalize_process_names(names)
    timestamp = now or _now()
    with _savepoint(conn, "save_manual_process_template"):
        manual = conn.execute(
            "SELECT id FROM manuals WHERE id = ?", (manual_id,)
        ).fetchone()
        if manual is None:
            raise ValueError("产品不存在")
        conn.execute(
            """
            INSERT INTO manual_process_configs (manual_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(manual_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (manual_id, timestamp, timestamp),
        )
        conn.execute(
            "DELETE FROM manual_process_steps WHERE manual_id = ?", (manual_id,)
        )
        for sort_order, name in enumerate(normalized):
            conn.execute(
                """
                INSERT INTO manual_process_steps (
                    manual_id, name, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (manual_id, name, sort_order, timestamp, timestamp),
            )
    return load_manual_process_template(conn, manual_id)


def load_followup_process_card(conn, followup_id):
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM production_followup_process_steps
            WHERE followup_id = ?
            ORDER BY sort_order, id
            """,
            (followup_id,),
        ).fetchall()
    ]


def create_followup_process_snapshot(
    conn, followup_id, manual_id=None, *, now=None
):
    timestamp = now or _now()
    with _savepoint(conn, "create_followup_process_snapshot"):
        followup = conn.execute(
            "SELECT id, manual_id, process_snapshot_created FROM production_followups WHERE id = ?",
            (followup_id,),
        ).fetchone()
        if followup is None:
            raise ValueError("生产跟进不存在")
        if manual_id is not None:
            try:
                requested_manual_id = int(manual_id)
            except (TypeError, ValueError):
                raise ValueError("生产跟进关联产品不匹配") from None
            if followup["manual_id"] != requested_manual_id:
                raise ValueError("生产跟进关联产品不匹配")

        existing = load_followup_process_card(conn, followup_id)
        if followup["process_snapshot_created"] or existing:
            if existing and not followup["process_snapshot_created"]:
                conn.execute(
                    """
                    UPDATE production_followups
                    SET process_snapshot_created = 1
                    WHERE id = ?
                    """,
                    (followup_id,),
                )
            return existing

        selected_manual_id = followup["manual_id"]
        template = (
            load_manual_process_template(conn, selected_manual_id)
            if selected_manual_id is not None
            else [
                {"name": name, "sort_order": sort_order}
                for sort_order, name in enumerate(DEFAULT_PROCESS_NAMES)
            ]
        )
        for sort_order, step in enumerate(template):
            conn.execute(
                """
                INSERT INTO production_followup_process_steps (
                    followup_id, name, sort_order, completed_at, completed_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, '', '', ?, ?)
                """,
                (followup_id, step["name"], sort_order, timestamp, timestamp),
            )
        conn.execute(
            """
            UPDATE production_followups
            SET process_snapshot_created = 1
            WHERE id = ?
            """,
            (followup_id,),
        )
    return load_followup_process_card(conn, followup_id)


def _card_step(card, step_id):
    try:
        normalized_id = int(step_id)
    except (TypeError, ValueError):
        raise ValueError("生产工艺不存在") from None
    for step in card:
        if step["id"] == normalized_id:
            return step
    raise ValueError("生产工艺不存在")


def _update_followup_timestamp(conn, followup_id, timestamp):
    updated = conn.execute(
        """
        UPDATE production_followups
        SET updated_at = ?
        WHERE id = ?
        """,
        (timestamp, followup_id),
    )
    if updated.rowcount != 1:
        raise ValueError("生产跟进不存在")


def _project_legacy_completion(
    conn, followup_id, process_name, completed_at, timestamp
):
    legacy_column = LEGACY_PROCESS_COLUMNS.get(process_name)
    if legacy_column is None:
        _update_followup_timestamp(conn, followup_id, timestamp)
        return
    updated = conn.execute(
        f"""
        UPDATE production_followups
        SET {legacy_column} = ?, updated_at = ?
        WHERE id = ?
        """,
        (completed_at, timestamp, followup_id),
    )
    if updated.rowcount != 1:
        raise ValueError("生产跟进不存在")


def complete_followup_process_step(
    conn, followup_id, step_id, completed_by, *, completed_at=None
):
    timestamp = completed_at or _beijing_now()
    operator = str(completed_by or "").strip()
    if not operator:
        raise ValueError("生产工艺操作人不能为空")
    with _savepoint(conn, "complete_followup_process_step"):
        card = load_followup_process_card(conn, followup_id)
        step = _card_step(card, step_id)
        if step["completed_at"]:
            raise ValueError("该生产工艺已经完成")
        first_uncompleted = next(
            (candidate for candidate in card if not candidate["completed_at"]), None
        )
        if first_uncompleted is None or first_uncompleted["id"] != step["id"]:
            raise ValueError("只能完成第一项未完成工艺")
        conn.execute(
            """
            UPDATE production_followup_process_steps
            SET completed_at = ?, completed_by = ?, updated_at = ?
            WHERE id = ? AND followup_id = ?
            """,
            (timestamp, operator, timestamp, step["id"], followup_id),
        )
        _project_legacy_completion(
            conn, followup_id, step["name"], timestamp, timestamp
        )
    return load_followup_process_card(conn, followup_id)


def revert_followup_process_step(conn, followup_id, step_id, *, now=None):
    timestamp = now or _beijing_now()
    with _savepoint(conn, "revert_followup_process_step"):
        card = load_followup_process_card(conn, followup_id)
        step = _card_step(card, step_id)
        if not step["completed_at"]:
            raise ValueError("该生产工艺尚未完成")
        completed = [candidate for candidate in card if candidate["completed_at"]]
        if not completed or completed[-1]["id"] != step["id"]:
            raise ValueError("只能撤回最后一项已完成工艺")
        conn.execute(
            """
            UPDATE production_followup_process_steps
            SET completed_at = '', completed_by = '', updated_at = ?
            WHERE id = ? AND followup_id = ?
            """,
            (timestamp, step["id"], followup_id),
        )
        _project_legacy_completion(
            conn, followup_id, step["name"], "", timestamp
        )
    return load_followup_process_card(conn, followup_id)


def add_followup_process_step(conn, followup_id, name, *, now=None):
    normalized_name = _normalize_process_names([name])[0]
    timestamp = now or _beijing_now()
    with _savepoint(conn, "add_followup_process_step"):
        card = load_followup_process_card(conn, followup_id)
        if any(
            step["name"].casefold() == normalized_name.casefold() for step in card
        ):
            raise ValueError("同一工艺卡的生产工艺名称不能重复")
        followup = conn.execute(
            "SELECT id FROM production_followups WHERE id = ?", (followup_id,)
        ).fetchone()
        if followup is None:
            raise ValueError("生产跟进不存在")
        conn.execute(
            """
            INSERT INTO production_followup_process_steps (
                followup_id, name, sort_order, completed_at, completed_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, '', '', ?, ?)
            """,
            (followup_id, normalized_name, len(card), timestamp, timestamp),
        )
        conn.execute(
            """
            UPDATE production_followups
            SET process_snapshot_created = 1, updated_at = ?
            WHERE id = ?
            """,
            (timestamp, followup_id),
        )
    return load_followup_process_card(conn, followup_id)


def _renumber_followup_process_steps(conn, followup_id, timestamp):
    card = load_followup_process_card(conn, followup_id)
    for sort_order, step in enumerate(card):
        if step["sort_order"] != sort_order:
            conn.execute(
                """
                UPDATE production_followup_process_steps
                SET sort_order = ?, updated_at = ?
                WHERE id = ? AND followup_id = ?
                """,
                (sort_order, timestamp, step["id"], followup_id),
            )


def delete_followup_process_step(conn, followup_id, step_id, *, now=None):
    timestamp = now or _beijing_now()
    with _savepoint(conn, "delete_followup_process_step"):
        card = load_followup_process_card(conn, followup_id)
        step = _card_step(card, step_id)
        if step["completed_at"]:
            raise ValueError("已完成工艺必须先撤回再删除")
        conn.execute(
            """
            DELETE FROM production_followup_process_steps
            WHERE id = ? AND followup_id = ?
            """,
            (step["id"], followup_id),
        )
        _renumber_followup_process_steps(conn, followup_id, timestamp)
        _project_legacy_completion(conn, followup_id, step["name"], "", timestamp)
    return load_followup_process_card(conn, followup_id)


def move_followup_process_step(
    conn, followup_id, step_id, direction, *, now=None
):
    if direction not in {"up", "down"}:
        raise ValueError("生产工艺移动方向无效")
    timestamp = now or _beijing_now()
    with _savepoint(conn, "move_followup_process_step"):
        card = load_followup_process_card(conn, followup_id)
        step = _card_step(card, step_id)
        if step["completed_at"]:
            raise ValueError("已完成工艺顺序不能调整")
        position = next(
            index for index, candidate in enumerate(card) if candidate["id"] == step["id"]
        )
        target_position = position - 1 if direction == "up" else position + 1
        if not 0 <= target_position < len(card):
            raise ValueError("生产工艺已经位于可移动边界")
        target = card[target_position]
        if target["completed_at"]:
            raise ValueError("未完成工艺不能跨越已完成工艺")
        conn.execute(
            """
            UPDATE production_followup_process_steps
            SET sort_order = ?, updated_at = ?
            WHERE id = ? AND followup_id = ?
            """,
            (target["sort_order"], timestamp, step["id"], followup_id),
        )
        conn.execute(
            """
            UPDATE production_followup_process_steps
            SET sort_order = ?, updated_at = ?
            WHERE id = ? AND followup_id = ?
            """,
            (step["sort_order"], timestamp, target["id"], followup_id),
        )
        _update_followup_timestamp(conn, followup_id, timestamp)
    return load_followup_process_card(conn, followup_id)
