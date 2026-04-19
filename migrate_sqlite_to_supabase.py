#!/usr/bin/env python3
"""
把当前本地 SQLite 报名数据迁移到 Supabase。

使用方式:
    1. 先复制 .env.example 为 .env.local，并填好 Supabase 配置
    2. 在 Supabase SQL Editor 中执行 supabase/schema.sql
    3. 运行:
       python3 migrate_sqlite_to_supabase.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from spring_trip_server import DATABASE_FILE, SupabaseStorage, load_env_files


def create_supabase_storage() -> SupabaseStorage:
    import os

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_secret_key = (
        os.getenv("SUPABASE_SECRET_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    table_name = os.getenv("SUPABASE_TABLE", "registrations").strip() or "registrations"

    if not supabase_url or not supabase_secret_key:
        raise RuntimeError(
            "请先在 .env.local 中设置 SUPABASE_URL 和 SUPABASE_SECRET_KEY。"
        )

    return SupabaseStorage(supabase_url, supabase_secret_key, table_name)


def fetch_sqlite_rows(database_file: Path) -> list[dict[str, object]]:
    if not database_file.exists():
        return []

    connection = sqlite3.connect(database_file)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, name, student_id, created_at
            FROM registrations
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def main() -> None:
    load_env_files()
    storage = create_supabase_storage()
    storage.prepare()

    rows = fetch_sqlite_rows(DATABASE_FILE)
    if not rows:
        print("本地 SQLite 中没有可迁移的报名数据。")
        return

    inserted = 0
    skipped = 0

    for row in rows:
        status_code, _ = storage.import_registration(row)
        if status_code == 201:
            inserted += 1
        elif status_code == 409:
            skipped += 1

    print(f"迁移完成：新增 {inserted} 条，跳过 {skipped} 条。")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"迁移失败：{exc}")
        raise SystemExit(1) from exc
