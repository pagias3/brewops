"""Read and write queries. All timestamps are naive local time strings."""

import sqlite3
from datetime import datetime, timedelta
from typing import Any


def _range_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """Turn inclusive 'YYYY-MM-DD' dates into a half-open [start, end) timestamp range.
    Either side may be None (unbounded)."""
    start = f"{date_from} 00:00:00" if date_from else None
    end = None
    if date_to:
        next_day = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        end = next_day.strftime("%Y-%m-%d 00:00:00")
    return start, end


def _range_conditions(
    column: str, date_from: str | None, date_to: str | None
) -> tuple[list[str], list[str]]:
    """Build SQL WHERE conditions for a date range. Returns (conditions, params)."""
    start, end = _range_bounds(date_from, date_to)
    conds, params = [], []
    if start:
        conds.append(f"{column} >= ?")
        params.append(start)
    if end:
        conds.append(f"{column} < ?")
        params.append(end)
    return conds, params


def get_machines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, floor, has_telemetry FROM machines ORDER BY id")
    return [dict(r) | {"has_telemetry": bool(r["has_telemetry"])} for r in rows]


def get_machine(conn: sqlite3.Connection, machine_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, floor, has_telemetry FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row) | {"has_telemetry": bool(row["has_telemetry"])}


def get_drink_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, label FROM drink_types ORDER BY id")
    return [dict(r) for r in rows]


def drink_type_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM drink_types WHERE name = ?", (name,)).fetchone()
    return row is not None


def insert_brew(
    conn: sqlite3.Connection,
    machine_id: int,
    drink_type: str,
    timestamp: str,
    duration_s: float,
    temp_c: float,
    source: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO brew_events (machine_id, drink_type, timestamp, duration_s, temp_c, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (machine_id, drink_type, timestamp, duration_s, temp_c, source),
    )
    return cur.lastrowid


def insert_maintenance(
    conn: sqlite3.Connection,
    machine_id: int,
    type: str,
    timestamp: str,
    note: str | None = None,
    error_code: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO maintenance_events (machine_id, type, timestamp, note, error_code)
        VALUES (?, ?, ?, ?, ?)
        """,
        (machine_id, type, timestamp, note, error_code),
    )
    return cur.lastrowid


def get_stats(
    conn: sqlite3.Connection, date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any]:
    """Dashboard numbers: totals, per-drink, per-day."""
    conds, params = _range_conditions("timestamp", date_from, date_to)
    where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM brew_events {where_clause}", params
    ).fetchone()["n"]

    # For per_drink, put range conditions in the ON clause to preserve drinks with zero count
    on_clause = ""
    if conds:
        be_conds = [f"be.{c}" for c in conds]
        on_clause = f"AND {' AND '.join(be_conds)}"

    per_drink = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT dt.name, dt.label, COUNT(be.id) AS count
            FROM drink_types dt
            LEFT JOIN brew_events be ON be.drink_type = dt.name {on_clause}
            GROUP BY dt.id
            ORDER BY dt.id
            """,
            params,
        )
    ]

    per_day = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT DATE(timestamp) AS day, COUNT(*) AS count
            FROM brew_events
            {where_clause}
            GROUP BY DATE(timestamp)
            ORDER BY day
            """,
            params,
        )
    ]
    return {"total_brews": total, "per_drink": per_drink, "per_day": per_day}


def get_machine_health(
    conn: sqlite3.Connection, machine_id: int, date_from: str | None = None, date_to: str | None = None
) -> dict[str, Any] | None:
    """Machine card: brew activity plus maintenance history."""
    machine = get_machine(conn, machine_id)
    if machine is None:
        return None

    conds, params = _range_conditions("be.timestamp", date_from, date_to)
    where_clause = f"WHERE be.machine_id = ? {f'AND {" AND ".join(conds)}' if conds else ''}"
    brew_params = [machine_id] + params

    brews = conn.execute(
        f"""
        SELECT COUNT(*) AS count, MAX(be.timestamp) AS last_brew
        FROM brew_events be
        {where_clause}
        """,
        brew_params,
    ).fetchone()

    # Most-brewed drink; ties go to the most recently brewed, then the lowest drink id.
    specialty_conds, specialty_params = _range_conditions("be.timestamp", date_from, date_to)
    specialty_where = f"WHERE be.machine_id = ? {f'AND {" AND ".join(specialty_conds)}' if specialty_conds else ''}"
    specialty_brew_params = [machine_id] + specialty_params

    specialty = conn.execute(
        f"""
        SELECT dt.name, dt.label, COUNT(*) AS count
        FROM brew_events be
        JOIN drink_types dt ON dt.name = be.drink_type
        {specialty_where}
        GROUP BY dt.id
        ORDER BY COUNT(*) DESC, MAX(be.timestamp) DESC, dt.id ASC
        LIMIT 1
        """,
        specialty_brew_params,
    ).fetchone()

    last_maintenance = conn.execute(
        """
        SELECT type, timestamp, note, error_code
        FROM maintenance_events
        WHERE machine_id = ? AND type != 'error'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (machine_id,),
    ).fetchone()
    recent_errors = [
        dict(r)
        for r in conn.execute(
            """
            SELECT timestamp, error_code, note
            FROM maintenance_events
            WHERE machine_id = ? AND type = 'error'
            ORDER BY timestamp DESC LIMIT 5
            """,
            (machine_id,),
        )
    ]
    return machine | {
        "brew_count": brews["count"],
        "last_brew": brews["last_brew"],
        "specialty": dict(specialty) if specialty else None,
        "last_maintenance": dict(last_maintenance) if last_maintenance else None,
        "recent_errors": recent_errors,
    }
