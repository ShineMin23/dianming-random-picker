#!/usr/bin/env python3
"""
中传春游活动报名服务

运行方式:
    python3 spring_trip_server.py
    python3 spring_trip_server.py 8080

默认使用本地 SQLite。
如需切换到 Supabase，请在 .env.local 中设置:
    REGISTRATION_BACKEND=supabase
    SUPABASE_URL=...
    SUPABASE_SECRET_KEY=...

管理后台:
    /admin
    需要在 .env.local 中配置 ADMIN_TOKEN
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
ADMIN_FILE = BASE_DIR / "admin.html"
DATA_DIR = BASE_DIR / "data"
DATABASE_FILE = DATA_DIR / "spring_trip_registrations.db"
TIMEZONE = ZoneInfo("Asia/Shanghai")
ENV_FILES = (BASE_DIR / ".env.local", BASE_DIR / ".env")
ADMIN_LIST_LIMIT = 5000


def now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now(TIMEZONE).strftime("%Y%m%d-%H%M%S")


def load_env_files() -> None:
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue

        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
                value = value[1:-1]

            os.environ.setdefault(key, value)


def normalize_name(value: Any) -> str:
    return "".join(str(value or "").strip().split())


def normalize_student_id(value: Any) -> str:
    return "".join(str(value or "").strip().split()).upper()


def mask_name(name: str) -> str:
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return f"{name[0]}*"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}"


def validate_payload(payload: dict[str, Any]) -> str:
    name = normalize_name(payload.get("name"))
    student_id = normalize_student_id(payload.get("studentId"))

    if not name:
        return "请填写姓名。"
    if len(name) < 2:
        return "姓名至少需要 2 个字符。"
    if not student_id:
        return "请填写学号。"
    if not student_id.isalnum() or not 6 <= len(student_id) <= 20:
        return "学号格式应为 6 到 20 位数字或字母。"
    return ""


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)


def display_datetime(value: str) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def registration_record(row: dict[str, Any]) -> dict[str, Any]:
    created_at = str(row.get("created_at", ""))
    raw_id = row.get("id")
    try:
        record_id = int(raw_id) if raw_id not in (None, "") else None
    except (TypeError, ValueError):
        record_id = None

    return {
        "id": record_id,
        "name": str(row.get("name", "")),
        "studentId": str(row.get("student_id", "")),
        "createdAt": created_at,
        "createdAtDisplay": display_datetime(created_at),
    }


def build_stats_payload(count: int, recent_rows: list[dict[str, Any]], database: str) -> dict[str, Any]:
    return {
        "count": count,
        "database": database,
        "generatedAt": now_iso(),
        "recent": [
            {
                "maskedName": mask_name(str(row["name"])),
                "createdAt": str(row["created_at"]),
            }
            for row in recent_rows
        ],
    }


def build_admin_payload(rows: list[dict[str, Any]], database: str) -> dict[str, Any]:
    return {
        "count": len(rows),
        "database": database,
        "generatedAt": now_iso(),
        "registrations": [registration_record(row) for row in rows],
    }


def build_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ID", "姓名", "学号", "报名时间"])
    for row in rows:
        writer.writerow(
            [
                row.get("id", ""),
                row.get("name", ""),
                row.get("student_id", ""),
                display_datetime(str(row.get("created_at", ""))),
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


class StorageBackend:
    label = "Database"

    def prepare(self) -> None:
        raise NotImplementedError

    def get_stats(self) -> dict[str, Any]:
        raise NotImplementedError

    def create_registration(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        raise NotImplementedError

    def list_registrations(self, limit: int = ADMIN_LIST_LIMIT) -> list[dict[str, Any]]:
        raise NotImplementedError


class SQLiteStorage(StorageBackend):
    label = "SQLite"

    def __init__(self, database_file: Path) -> None:
        self.database_file = database_file

    def _connection(self) -> sqlite3.Connection:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_file, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def prepare(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    student_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_registrations_created_at
                ON registrations (created_at DESC)
                """
            )
            connection.commit()

    def get_stats(self) -> dict[str, Any]:
        with self._connection() as connection:
            count_row = connection.execute(
                "SELECT COUNT(*) AS count FROM registrations"
            ).fetchone()
            recent_rows = connection.execute(
                """
                SELECT name, created_at
                FROM registrations
                ORDER BY id DESC
                LIMIT 5
                """
            ).fetchall()

        return build_stats_payload(
            count=int(count_row["count"]) if count_row else 0,
            recent_rows=[
                {"name": str(row["name"]), "created_at": str(row["created_at"])}
                for row in recent_rows
            ],
            database=self.label,
        )

    def create_registration(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST, {"message": "请求体必须是 JSON 对象。"}

        error_message = validate_payload(payload)
        if error_message:
            return HTTPStatus.BAD_REQUEST, {"message": error_message}

        name = normalize_name(payload.get("name"))
        student_id = normalize_student_id(payload.get("studentId"))
        created_at = now_iso()

        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT name, student_id, created_at
                FROM registrations
                WHERE student_id = ?
                """,
                (student_id,),
            ).fetchone()

            if existing:
                return HTTPStatus.CONFLICT, {
                    "message": f"学号 {student_id} 已报名，无需重复提交。",
                    "createdAt": str(existing["created_at"]),
                }

            try:
                connection.execute(
                    """
                    INSERT INTO registrations (name, student_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (name, student_id, created_at),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                return HTTPStatus.CONFLICT, {
                    "message": f"学号 {student_id} 已报名，无需重复提交。",
                }

        return HTTPStatus.CREATED, {
            "message": f"{name} 报名成功，信息已写入数据库。",
            "createdAt": created_at,
        }

    def list_registrations(self, limit: int = ADMIN_LIST_LIMIT) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, student_id, created_at
                FROM registrations
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "name": str(row["name"]),
                "student_id": str(row["student_id"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]


class SupabaseStorage(StorageBackend):
    label = "Supabase"

    def __init__(self, project_url: str, secret_key: str, table_name: str = "registrations") -> None:
        self.project_url = project_url.rstrip("/")
        self.secret_key = secret_key
        self.table_name = table_name
        self.table_url = (
            f"{self.project_url}/rest/v1/{urllib.parse.quote(self.table_name, safe='')}"
        )
        self.base_headers = {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
        }

    def prepare(self) -> None:
        status_code, _, payload = self._request(
            "GET",
            query={"select": "id", "limit": "1"},
        )

        if status_code == HTTPStatus.OK:
            return

        if status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise RuntimeError(
                "Supabase 鉴权失败，请检查 SUPABASE_SECRET_KEY 或 SUPABASE_SERVICE_ROLE_KEY 是否正确。"
            )

        if status_code == HTTPStatus.NOT_FOUND:
            raise RuntimeError(
                f"Supabase 中未找到表 `{self.table_name}`。请先在 SQL Editor 中执行 supabase/schema.sql。"
            )

        raise RuntimeError(self._error_message("Supabase 初始化失败", status_code, payload))

    def get_stats(self) -> dict[str, Any]:
        rows = self.list_registrations(limit=5)
        return build_stats_payload(
            count=self._count_all_rows(),
            recent_rows=rows,
            database=self.label,
        )

    def create_registration(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST, {"message": "请求体必须是 JSON 对象。"}

        error_message = validate_payload(payload)
        if error_message:
            return HTTPStatus.BAD_REQUEST, {"message": error_message}

        name = normalize_name(payload.get("name"))
        student_id = normalize_student_id(payload.get("studentId"))
        created_at = now_iso()

        status_code, _, response_payload = self._request(
            "POST",
            query={"select": "id,name,student_id,created_at"},
            body=[
                {
                    "name": name,
                    "student_id": student_id,
                    "created_at": created_at,
                }
            ],
            extra_headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        )

        if status_code in (HTTPStatus.OK, HTTPStatus.CREATED):
            rows = response_payload if isinstance(response_payload, list) else []
            created_row = rows[0] if rows else {}
            return HTTPStatus.CREATED, {
                "message": f"{name} 报名成功，信息已写入数据库。",
                "createdAt": str(created_row.get("created_at", created_at)),
            }

        if status_code == HTTPStatus.CONFLICT:
            existing = self._get_existing_registration(student_id)
            result = {"message": f"学号 {student_id} 已报名，无需重复提交。"}
            if existing.get("created_at"):
                result["createdAt"] = str(existing["created_at"])
            return HTTPStatus.CONFLICT, result

        raise RuntimeError(self._error_message("写入 Supabase 报名数据失败", status_code, response_payload))

    def import_registration(self, record: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        payload = {
            "name": normalize_name(record.get("name")),
            "student_id": normalize_student_id(record.get("student_id")),
            "created_at": str(record.get("created_at") or now_iso()),
        }
        record_id = record.get("id")
        if record_id not in (None, ""):
            payload["id"] = int(record_id)

        status_code, _, response_payload = self._request(
            "POST",
            query={"select": "id,student_id,created_at"},
            body=[payload],
            extra_headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        )

        if status_code in (HTTPStatus.OK, HTTPStatus.CREATED):
            rows = response_payload if isinstance(response_payload, list) else []
            inserted = rows[0] if rows else {}
            return HTTPStatus.CREATED, {
                "studentId": str(inserted.get("student_id", payload["student_id"])),
                "createdAt": str(inserted.get("created_at", payload["created_at"])),
            }

        if status_code == HTTPStatus.CONFLICT:
            return HTTPStatus.CONFLICT, {
                "studentId": payload["student_id"],
                "message": f"学号 {payload['student_id']} 已存在，已跳过。",
            }

        raise RuntimeError(self._error_message("迁移数据到 Supabase 失败", status_code, response_payload))

    def list_registrations(self, limit: int = ADMIN_LIST_LIMIT) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000

        while len(results) < limit:
            current_limit = min(page_size, limit - len(results))
            status_code, _, payload = self._request(
                "GET",
                query={
                    "select": "id,name,student_id,created_at",
                    "order": "id.desc",
                    "limit": str(current_limit),
                    "offset": str(offset),
                },
            )

            if status_code != HTTPStatus.OK:
                raise RuntimeError(self._error_message("读取 Supabase 报名列表失败", status_code, payload))

            rows = payload if isinstance(payload, list) else []
            normalized = [
                {
                    "id": row.get("id"),
                    "name": str(row.get("name", "")),
                    "student_id": str(row.get("student_id", "")),
                    "created_at": str(row.get("created_at", "")),
                }
                for row in rows
            ]
            results.extend(normalized)

            if len(rows) < current_limit:
                break

            offset += current_limit

        return results[:limit]

    def _count_all_rows(self) -> int:
        status_code, headers, payload = self._request(
            "GET",
            query={"select": "id", "limit": "1"},
            extra_headers={"Prefer": "count=exact"},
        )

        if status_code != HTTPStatus.OK:
            raise RuntimeError(self._error_message("读取 Supabase 统计失败", status_code, payload))

        return self._count_from_headers(headers)

    def _get_existing_registration(self, student_id: str) -> dict[str, Any]:
        status_code, _, payload = self._request(
            "GET",
            query={
                "select": "name,student_id,created_at",
                "student_id": f"eq.{student_id}",
                "limit": "1",
            },
        )

        if status_code != HTTPStatus.OK or not isinstance(payload, list) or not payload:
            return {}
        return payload[0]

    def _request(
        self,
        method: str,
        query: dict[str, str] | None = None,
        body: Any | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        query_string = urllib.parse.urlencode(query or {}, doseq=True)
        url = self.table_url if not query_string else f"{self.table_url}?{query_string}"
        request_body = None
        if body is not None:
            request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(url, data=request_body, method=method)
        headers = dict(self.base_headers)
        if extra_headers:
            headers.update(extra_headers)

        for key, value in headers.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                response_headers = dict(response.headers.items())
                raw = response.read()
                data = json.loads(raw.decode("utf-8")) if raw else None
                return response.status, response_headers, data
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            data = None
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    data = {"message": raw.decode("utf-8", errors="replace")}
            return exc.code, dict(exc.headers.items()), data
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "无法连接到 Supabase，请检查 SUPABASE_URL 是否正确，以及当前网络是否可访问。"
            ) from exc

    @staticmethod
    def _count_from_headers(headers: dict[str, str]) -> int:
        content_range = headers.get("Content-Range", "")
        if "/" not in content_range:
            return 0
        total = content_range.split("/", 1)[1]
        return int(total) if total.isdigit() else 0

    @staticmethod
    def _error_message(prefix: str, status_code: int, payload: Any) -> str:
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or "").strip()
            details = str(payload.get("details") or payload.get("hint") or "").strip()
        else:
            message = ""
            details = ""

        if status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return "Supabase 鉴权失败，请检查项目密钥是否正确。"
        if status_code == HTTPStatus.NOT_FOUND:
            return "Supabase 资源不存在，请检查项目 URL、表名，或是否已经执行建表 SQL。"

        combined = " ".join(part for part in (message, details) if part)
        if combined:
            return f"{prefix}：{combined}"
        return f"{prefix}：HTTP {status_code}"


def create_storage_from_env() -> StorageBackend:
    backend = os.getenv("REGISTRATION_BACKEND", "sqlite").strip().lower()
    if backend in ("", "sqlite"):
        return SQLiteStorage(DATABASE_FILE)
    if backend != "supabase":
        raise RuntimeError(
            "REGISTRATION_BACKEND 仅支持 `sqlite` 或 `supabase`。"
        )

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_secret_key = (
        os.getenv("SUPABASE_SECRET_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    table_name = os.getenv("SUPABASE_TABLE", "registrations").strip() or "registrations"

    if not supabase_url or not supabase_secret_key:
        raise RuntimeError(
            "启用 Supabase 前，请在 .env.local 中设置 SUPABASE_URL 和 SUPABASE_SECRET_KEY。"
        )

    return SupabaseStorage(supabase_url, supabase_secret_key, table_name)


class SpringTripHandler(BaseHTTPRequestHandler):
    server_version = "SpringTripServer/3.0"

    @property
    def storage(self) -> StorageBackend:
        return self.server.storage  # type: ignore[attr-defined]

    @property
    def admin_token(self) -> str:
        return self.server.admin_token  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)

            if parsed.path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "generatedAt": now_iso(),
                        "database": self.storage.label,
                    },
                )
                return

            if parsed.path == "/api/registrations/stats":
                self._send_json(HTTPStatus.OK, self.storage.get_stats())
                return

            if parsed.path == "/api/admin/meta":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "configured": bool(self.admin_token),
                        "database": self.storage.label,
                        "generatedAt": now_iso(),
                        "adminPath": "/admin",
                    },
                    extra_headers={"Cache-Control": "no-store"},
                )
                return

            if parsed.path == "/api/admin/registrations":
                if not self._require_admin_access(parsed):
                    return
                rows = self.storage.list_registrations(limit=ADMIN_LIST_LIMIT)
                self._send_json(
                    HTTPStatus.OK,
                    build_admin_payload(rows, self.storage.label),
                    extra_headers={"Cache-Control": "no-store"},
                )
                return

            if parsed.path == "/api/admin/export.csv":
                if not self._require_admin_access(parsed):
                    return
                rows = self.storage.list_registrations(limit=ADMIN_LIST_LIMIT)
                filename = f"cuc-spring-trip-registrations-{now_stamp()}.csv"
                self._send_bytes(
                    HTTPStatus.OK,
                    build_csv_bytes(rows),
                    "text/csv; charset=utf-8",
                    extra_headers={
                        "Cache-Control": "no-store",
                        "Content-Disposition": f'attachment; filename="{filename}"',
                    },
                )
                return

            if parsed.path in ("/", "/index.html"):
                self._send_file(INDEX_FILE, "text/html; charset=utf-8")
                return

            if parsed.path in ("/admin", "/admin.html"):
                self._send_file(ADMIN_FILE, "text/html; charset=utf-8")
                return

            asset_path = (BASE_DIR / parsed.path.lstrip("/")).resolve()
            if asset_path.is_file() and BASE_DIR in asset_path.parents:
                self._send_file(asset_path, self._guess_content_type(asset_path))
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"message": "资源不存在。"})
        except RuntimeError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"message": str(exc)})
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"message": "服务内部错误。"})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/api/registrations":
                self._send_json(HTTPStatus.NOT_FOUND, {"message": "接口不存在。"})
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""

            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"message": "请求体必须是合法 JSON。"})
                return

            status_code, data = self.storage.create_registration(payload)
            self._send_json(status_code, data)
        except RuntimeError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"message": str(exc)})
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"message": "服务内部错误。"})

    def log_message(self, format: str, *args: Any) -> None:
        message = "%s - - [%s] %s\n" % (
            self.address_string(),
            self.log_date_time_string(),
            format % args,
        )
        sys.stderr.write(message)

    def _require_admin_access(self, parsed: Any) -> bool:
        if not self.admin_token:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"message": "后台未启用，请先在 .env.local 中设置 ADMIN_TOKEN。"},
                extra_headers={"Cache-Control": "no-store"},
            )
            return False

        provided = self._read_admin_token(parsed)
        if not provided or not hmac.compare_digest(provided, self.admin_token):
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"message": "后台口令错误或未提供，请重新输入。"},
                extra_headers={"Cache-Control": "no-store"},
            )
            return False

        return True

    def _read_admin_token(self, parsed: Any) -> str:
        direct = self.headers.get("X-Admin-Token", "").strip()
        if direct:
            return direct

        authorization = self.headers.get("Authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()

        query = parse_qs(parsed.query)
        return (query.get("token") or [""])[0].strip()

    def _send_file(self, file_path: Path, content_type: str) -> None:
        self._send_bytes(HTTPStatus.OK, file_path.read_bytes(), content_type)

    def _send_json(
        self,
        status_code: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status_code, content, "application/json; charset=utf-8", extra_headers)

    def _send_bytes(
        self,
        status_code: int,
        content: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _guess_content_type(file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".html":
            return "text/html; charset=utf-8"
        if suffix == ".css":
            return "text/css; charset=utf-8"
        if suffix == ".js":
            return "application/javascript; charset=utf-8"
        if suffix == ".json":
            return "application/json; charset=utf-8"
        if suffix == ".svg":
            return "image/svg+xml"
        if suffix == ".png":
            return "image/png"
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".csv":
            return "text/csv; charset=utf-8"
        return "application/octet-stream"


def main() -> None:
    try:
        load_env_files()
        storage = create_storage_from_env()
        storage.prepare()
    except RuntimeError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("PORT", "8080"))
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()

    server = ThreadingHTTPServer(("0.0.0.0", port), SpringTripHandler)
    server.storage = storage  # type: ignore[attr-defined]
    server.admin_token = admin_token  # type: ignore[attr-defined]

    print(f"中传春游活动报名页已启动：http://127.0.0.1:{port}")
    print(f"当前数据后端：{storage.label}")
    if admin_token:
        print(f"管理后台已启用：http://127.0.0.1:{port}/admin")
    else:
        print("管理后台未启用：请在 .env.local 中设置 ADMIN_TOKEN")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
