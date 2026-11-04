"""Copy one hosted account from a SQLite data folder into Postgres, keeping ids, timestamps and history.

    DATABASE_URL=postgresql://... python scripts/migrate_account_to_postgres.py <data-dir> <user-id>

The account's password hash moves with it, so the same email and password keep working. It refuses to run if the
target account already has memory. App sign-ins and connected apps are not copied: apps connect again on the new
address. The source files are only read.
"""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memlayer.accounts import AccountStore  # noqa: E402

# Parents first, so foreign keys hold. records.superseded_by is filled in afterwards because it points at records.
TABLES = ["projects", "sources", "records", "evidence", "relations", "record_history"]


def rows(db: sqlite3.Connection, table: str) -> list[dict]:
    order = "id" if table == "record_history" else "rowid"
    return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def main(data_dir: str, user_id: str, url: str) -> None:
    source_accounts = sqlite3.connect(f"file:{Path(data_dir) / 'accounts.db'}?mode=ro", uri=True)
    source_accounts.row_factory = sqlite3.Row
    user = source_accounts.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        sys.exit(f"No user {user_id} in {data_dir}")
    source = sqlite3.connect(f"file:{Path(data_dir) / 'users' / user_id / 'memory.db'}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    accounts = AccountStore(url)
    existing = accounts.conn.execute("SELECT id FROM users WHERE email = ?", (user["email"].lower(),)).fetchone()
    if existing and existing["id"] != user_id:
        sys.exit("That email already has a different account on the target. Nothing was changed.")
    if not existing:
        with accounts._tx() as c:
            c.execute("INSERT INTO users (id, email, password_hash, created_at, last_login_at, disabled) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (user_id, user["email"].lower(), user["password_hash"], user["created_at"],
                       user["last_login_at"], user["disabled"]))

    store = accounts.memory_for(user_id)
    if store.list_projects():
        sys.exit("The target account already has projects. Nothing was changed.")

    supersessions = []
    with store._write() as c:  # all or nothing
        for table in TABLES:
            for row in rows(source, table):
                if table == "record_history":
                    row.pop("id")  # the target numbers history itself, in the same order
                if table == "records" and row["superseded_by"]:
                    supersessions.append((row["superseded_by"], row["id"]))
                    row["superseded_by"] = None
                cols = ", ".join(row)
                marks = ", ".join("?" * len(row))
                c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
        for newer, older in supersessions:
            c.execute("UPDATE records SET superseded_by = ? WHERE id = ?", (newer, older))

    for table in TABLES:
        want = source.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        with store._read() as c:
            got = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"{table}: {got} of {want}")
        if want != got:
            sys.exit(f"Count mismatch in {table}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or not os.environ.get("DATABASE_URL"):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], os.environ["DATABASE_URL"])
