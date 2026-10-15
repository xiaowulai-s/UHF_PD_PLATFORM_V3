# -*- coding: utf-8 -*-
"""SQLite migration utility for aligning legacy databases with the ORM schema."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

DB_PATH = Path("data") / "equipment_management.db"
BACKUP_SUFFIX = ".backup"

DEVICE_COLUMNS = (
    ("id", "VARCHAR(64) PRIMARY KEY"),
    ("name", "VARCHAR(128) NOT NULL"),
    ("device_type", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("device_number", "VARCHAR(64)"),
    ("protocol_type", "VARCHAR(32) NOT NULL DEFAULT 'modbus_tcp'"),
    ("host", "VARCHAR(64)"),
    ("port", "INTEGER"),
    ("unit_id", "INTEGER DEFAULT 1"),
    ("use_simulator", "BOOLEAN DEFAULT 0"),
    ("status", "INTEGER DEFAULT 0"),
    ("last_connected_at", "DATETIME"),
    ("connection_count", "INTEGER DEFAULT 0"),
    ("error_count", "INTEGER DEFAULT 0"),
    ("created_at", "DATETIME"),
    ("updated_at", "DATETIME"),
)

REGISTER_MAP_COLUMNS = (
    ("function_code", "INTEGER DEFAULT 3"),
    ("data_type", "VARCHAR(32) DEFAULT 'uint16'"),
    ("read_write", "VARCHAR(8) DEFAULT 'R'"),
    ("scale", "FLOAT DEFAULT 1.0"),
    ("unit", "VARCHAR(32) DEFAULT ''"),
    ("description", "TEXT"),
    ("enabled", "BOOLEAN DEFAULT 1"),
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_device_type ON devices (device_type)",
    "CREATE INDEX IF NOT EXISTS idx_device_name ON devices (name)",
    "CREATE INDEX IF NOT EXISTS idx_register_device ON register_maps (device_id)",
    "CREATE INDEX IF NOT EXISTS idx_register_address ON register_maps (device_id, address)",
)


def get_db_path() -> Path:
    """Return the database path."""
    return DB_PATH


def get_table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
    """Return the set of column names for a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    """Return whether a table exists."""
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cursor.fetchone() is not None


def check_and_add_column(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    column_type: str,
) -> bool:
    """Add one missing column to a table."""
    columns = get_table_columns(cursor, table_name)
    if column_name in columns:
        print(f"Column already exists: {table_name}.{column_name}")
        return False

    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
    print(f"Added column: {table_name}.{column_name}")
    return True


def create_devices_table(cursor: sqlite3.Cursor) -> None:
    """Create the ORM-compatible devices table."""
    column_sql = ", ".join(f"{name} {definition}" for name, definition in DEVICE_COLUMNS)
    cursor.execute(f"CREATE TABLE devices ({column_sql})")


def migrate_legacy_devices_table(cursor: sqlite3.Cursor) -> bool:
    """Replace a legacy devices table with the current schema."""
    if not table_exists(cursor, "devices"):
        print("devices table does not exist, creating a new one.")
        create_devices_table(cursor)
        return True

    columns = get_table_columns(cursor, "devices")
    has_target_schema = {"id", "name", "device_type", "protocol_type", "host", "unit_id"}.issubset(columns)
    legacy_markers = {"device_id", "ip_address", "slave_id"} & columns

    if has_target_schema and not legacy_markers:
        print("devices table is already compatible with the current schema.")
        return False

    if not legacy_markers and not has_target_schema:
        raise RuntimeError(f"Unsupported devices schema: {sorted(columns)}")

    print("Migrating legacy devices table to the current schema.")
    cursor.execute("ALTER TABLE devices RENAME TO devices_legacy")
    create_devices_table(cursor)

    def legacy_expr(column_name: str, fallback_sql: str = "NULL") -> str:
        return column_name if column_name in columns else fallback_sql

    cursor.execute(
        f"""
        INSERT INTO devices (
            id, name, device_type, device_number, protocol_type, host, port, unit_id,
            use_simulator, status, last_connected_at, connection_count, error_count,
            created_at, updated_at
        )
        SELECT
            COALESCE(NULLIF(TRIM(CAST(device_id AS TEXT)), ''), CAST(id AS TEXT)),
            COALESCE(name, 'Unnamed Device'),
            COALESCE(NULLIF({legacy_expr("device_type")}, ''), 'Unknown'),
            NULL,
            COALESCE(NULLIF({legacy_expr("protocol_type")}, ''), 'modbus_tcp'),
            COALESCE(NULLIF({legacy_expr("host")}, ''), {legacy_expr("ip_address")}),
            {legacy_expr("port")},
            COALESCE({legacy_expr("unit_id")}, {legacy_expr("slave_id")}, 1),
            COALESCE({legacy_expr("use_simulator")}, 0),
            COALESCE({legacy_expr("status")}, 0),
            {legacy_expr("last_connected_at")},
            COALESCE({legacy_expr("connection_count")}, 0),
            COALESCE({legacy_expr("error_count")}, 0),
            {legacy_expr("created_at")},
            {legacy_expr("updated_at")}
        FROM devices_legacy
        """
    )
    cursor.execute("DROP TABLE devices_legacy")
    print("devices table migration completed.")
    return True


def migrate_register_maps_table(cursor: sqlite3.Cursor) -> bool:
    """Ensure register_maps contains the required columns."""
    if not table_exists(cursor, "register_maps"):
        print("register_maps table not found, skipping column migration.")
        return False

    print("\n=== Migrating register_maps table ===")
    changed = False
    for column_name, column_type in REGISTER_MAP_COLUMNS:
        changed = check_and_add_column(cursor, "register_maps", column_name, column_type) or changed
    return changed


def create_indexes(cursor: sqlite3.Cursor) -> None:
    """Create required indexes if they are missing."""
    print("\n=== Creating indexes ===")
    for statement in INDEX_STATEMENTS:
        cursor.execute(statement)
        print(statement.replace("CREATE INDEX IF NOT EXISTS ", "Ensured index: "))


def copy_backup(db_path: Path) -> Path:
    """Create a filesystem backup of the database."""
    backup_path = db_path.with_name(db_path.name + BACKUP_SUFFIX)
    shutil.copy2(db_path, backup_path)
    return backup_path


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """Restore the database from a backup file."""
    shutil.copy2(backup_path, db_path)


def run_migration(cursor: sqlite3.Cursor) -> bool:
    """Execute all migration steps and return whether anything changed."""
    print("\n=== Migrating devices table ===")
    changed = migrate_legacy_devices_table(cursor)
    changed = migrate_register_maps_table(cursor) or changed
    create_indexes(cursor)
    return changed


def main() -> int:
    """Run the migration workflow."""
    db_path = get_db_path()

    print("=" * 60)
    print("Database Migration Tool")
    print("=" * 60)
    print(f"Database path: {db_path}")

    if not db_path.exists():
        print("Database file does not exist. Start the application once to create it.")
        return 1

    backup_path = copy_backup(db_path)
    print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        changed = run_migration(conn.cursor())
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        print("\n" + "=" * 60)
        print("Database migration completed successfully.")
        if not changed:
            print("No schema changes were necessary.")
        print("=" * 60)
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"\nMigration failed: {exc}")
        print("Restoring backup.")
        restore_backup(backup_path, db_path)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
