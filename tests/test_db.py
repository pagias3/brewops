import pytest

from brewops.db.connection import connect
from brewops.db.queries import (
    get_drink_types,
    get_machine_health,
    get_machines,
    get_stats,
    insert_brew,
    insert_maintenance,
)
from brewops.db.schema import init_db, reset_db


@pytest.fixture
def conn(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


def test_init_is_idempotent(conn):
    init_db(conn)
    init_db(conn)
    assert len(get_machines(conn)) == 4
    assert len(get_drink_types(conn)) == 6


def test_reference_data_seeded(conn):
    machines = {m["name"]: m for m in get_machines(conn)}
    assert "Bertha (3rd floor)" in machines
    assert machines["Old Faithful (2nd floor)"]["has_telemetry"] is False
    drinks = {d["name"] for d in get_drink_types(conn)}
    assert "espresso" in drinks


def test_stats_math(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 3, "lungo", "2026-06-02 11:00:00", 38.0, 90.0, "manual")
    conn.commit()

    stats = get_stats(conn)
    assert stats["total_brews"] == 4
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 2
    assert per_drink["latte"] == 1
    assert per_drink["cappuccino"] == 0
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 2}


def test_machine_health(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health = get_machine_health(conn, 4)
    assert health["brew_count"] == 1
    assert health["last_brew"] == "2026-06-01 08:00:00"
    assert health["last_maintenance"]["type"] == "descale"
    assert health["recent_errors"][0]["error_code"] == "E42"


def test_machine_health_unknown_machine(conn):
    assert get_machine_health(conn, 999) is None


def test_specialty_is_most_brewed_with_label(conn):
    insert_brew(conn, 1, "hot_water", "2026-06-01 08:00:00", 10.0, 95.0, "csv")
    insert_brew(conn, 1, "hot_water", "2026-06-01 09:00:00", 10.0, 95.0, "csv")
    insert_brew(conn, 1, "latte", "2026-06-02 09:00:00", 40.0, 88.0, "csv")
    insert_brew(conn, 2, "espresso", "2026-06-05 09:00:00", 27.0, 92.0, "csv")
    conn.commit()

    assert get_machine_health(conn, 1)["specialty"] == {
        "name": "hot_water", "label": "Hot water", "count": 2,
    }
    # counted per machine
    assert get_machine_health(conn, 2)["specialty"]["name"] == "espresso"


def test_specialty_tie_goes_to_most_recent(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "latte", "2026-06-02 08:00:00", 40.0, 88.0, "csv")
    conn.commit()
    assert get_machine_health(conn, 1)["specialty"]["name"] == "latte"

    insert_brew(conn, 1, "espresso", "2026-06-03 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "latte", "2026-06-03 08:00:00", 40.0, 88.0, "csv")
    conn.commit()
    # tied on count and timestamp: lowest drink id (espresso) wins
    assert get_machine_health(conn, 1)["specialty"]["name"] == "espresso"


def test_specialty_none_without_brews(conn):
    assert get_machine_health(conn, 4)["specialty"] is None


def test_reset_db_clears_events(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    conn.commit()
    reset_db(conn)
    assert get_stats(conn)["total_brews"] == 0
    assert len(get_machines(conn)) == 4


def test_stats_date_range_inclusive_bounds(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 00:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-02 23:59:59", 26.0, 91.5, "csv")
    insert_brew(conn, 1, "espresso", "2026-05-31 23:59:59", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-03 00:00:00", 27.0, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, "2026-06-01", "2026-06-02")
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 1, "2026-06-02": 1}


def test_stats_from_only(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-05 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 1, "espresso", "2026-05-31 23:59:59", 27.0, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, "2026-06-01", None)
    assert stats["total_brews"] == 2


def test_stats_to_only(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-05 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 1, "espresso", "2026-05-31 23:59:59", 27.0, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, None, "2026-06-01")
    assert stats["total_brews"] == 2


def test_stats_single_day(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-02 10:00:00", 27.0, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, "2026-06-01", "2026-06-01")
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2}


def test_stats_empty_range_keeps_all_drinks(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, "2026-07-01", "2026-07-31")
    assert stats["total_brews"] == 0
    assert len(stats["per_day"]) == 0
    assert len(stats["per_drink"]) == 6
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert all(count == 0 for count in per_drink.values())


def test_stats_unfiltered_unchanged(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "latte", "2026-06-02 09:00:00", 40.0, 88.0, "csv")
    conn.commit()

    stats_unfiltered = get_stats(conn)
    stats_none = get_stats(conn, None, None)
    assert stats_unfiltered == stats_none


def test_machine_health_range(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-05 09:00:00", 26.0, 91.5, "csv")
    insert_maintenance(conn, 1, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 1, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health_range = get_machine_health(conn, 1, "2026-06-02", "2026-06-04")
    assert health_range["brew_count"] == 0
    assert health_range["specialty"] is None
    assert health_range["last_maintenance"]["type"] == "descale"
    assert health_range["recent_errors"][0]["error_code"] == "E42"


def test_machine_health_empty_range(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    conn.commit()

    health = get_machine_health(conn, 1, "2026-07-01", "2026-07-31")
    assert health["brew_count"] == 0
    assert health["last_brew"] is None
    assert health["specialty"] is None
