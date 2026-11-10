#!/usr/bin/env python3
"""Copy a consistent SQLite snapshot into empty PostgreSQL application tables.

Usage: run from the Prism Link project root using its virtual environment.
This script does not modify the live SQLite database or application settings.
"""
import getpass
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, time
from urllib.parse import quote

from sqlalchemy import MetaData, create_engine, inspect, select, func

SOURCE = "prism_link.db"
TABLE_ORDER = [
    "tenants", "users", "services", "business_hours", "staff_members",
    "calendar_connections", "calls", "appointments", "audit_logs",
    "call_summaries", "salon_messages", "transcript_messages",
]


def convert(value, column_type):
    if value is None:
        return None
    kind = column_type.__class__.__name__.upper()
    if kind == "UUID":
        return uuid.UUID(str(value))
    if kind in ("TIMESTAMP", "DATETIME"):
        return datetime.fromisoformat(str(value))
    if kind == "TIME":
        return time.fromisoformat(str(value))
    if kind == "BOOLEAN":
        if value not in (0, 1, True, False):
            raise ValueError(f"Invalid boolean: {value!r}")
        return bool(value)
    return value


def main():
    if not os.path.isfile(SOURCE):
        raise SystemExit(f"Cannot find {SOURCE}; run from project root")
    password = quote(getpass.getpass("PostgreSQL password: "), safe="")
    url = f"postgresql+psycopg://prism_link_app:{password}@127.0.0.1:5432/prism_link"
    engine = create_engine(url)
    fd, snapshot = tempfile.mkstemp(prefix="prism-link-snapshot-", suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(SOURCE) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
        print("Consistent SQLite snapshot created.")
        with sqlite3.connect(snapshot) as source:
            source.row_factory = sqlite3.Row
            metadata = MetaData()
            metadata.reflect(bind=engine, only=TABLE_ORDER)
            with engine.begin() as connection:
                # Refuse to overwrite or duplicate anything.
                for name in TABLE_ORDER:
                    table = metadata.tables[name]
                    if connection.execute(select(func.count()).select_from(table)).scalar_one():
                        raise RuntimeError(f"PostgreSQL table {name} is not empty; no changes made")
                total = 0
                for name in TABLE_ORDER:
                    table = metadata.tables[name]
                    rows = source.execute(f'SELECT * FROM "{name}"')
                    column_names = {column.name for column in table.columns}
                    batch = []
                    inserted = 0
                    for row in rows:
                        record = dict(row)
                        if set(record) != column_names:
                            raise RuntimeError(f"Column mismatch in {name}")
                        batch.append({col.name: convert(record[col.name], col.type) for col in table.columns})
                        if len(batch) == 250:
                            connection.execute(table.insert(), batch)
                            inserted += len(batch)
                            batch.clear()
                    if batch:
                        connection.execute(table.insert(), batch)
                        inserted += len(batch)
                    target_count = connection.execute(select(func.count()).select_from(table)).scalar_one()
                    if target_count != inserted:
                        raise RuntimeError(f"Count mismatch in {name}: {inserted} vs {target_count}")
                    print(f"OK {name}: {inserted}")
                    total += inserted
                # PostgreSQL checks foreign keys as rows are inserted.
                print(f"Verified {total} application records. Committing test copy.")
        print("TEST MIGRATION SUCCESSFUL. Live SQLite and service settings unchanged.")
    finally:
        engine.dispose()
        try:
            os.remove(snapshot)
        except OSError:
            pass


if __name__ == "__main__":
    main()
