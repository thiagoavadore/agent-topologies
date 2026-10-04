"""Plan tomorrow's repair routes from mileage and fault reports, and write them for the tablet app."""

import sqlite3

DATA_DIR = "/var/lib/maintenance"


def plan_routes() -> list[tuple[str, int]]:
    with sqlite3.connect(f"{DATA_DIR}/history.sqlite") as history:
        return history.execute("SELECT bike_id, km_since_service FROM bikes ORDER BY 2 DESC").fetchall()


def write_plan(routes: list[tuple[str, int]]) -> None:
    with sqlite3.connect(f"{DATA_DIR}/plan.sqlite") as plan:
        plan.execute("DELETE FROM routes")
        plan.executemany("INSERT INTO routes VALUES (?, ?)", routes)


if __name__ == "__main__":
    write_plan(plan_routes())
