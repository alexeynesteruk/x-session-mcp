"""MySQL session for x-session-mcp.

One row in `mcp_x.session` holds the X cookies. The MySQL password stays in
the environment (`X_DATABASE_URL`). HTTP mode never reads those cookies from
the environment; it loads this row only after the caller presents `X_HTTP_TOKEN`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

# PyMySQL calls getpass.getuser() at import. The router container is uid 1600
# (egress) and may have no matching passwd entry, so set a name first.
os.environ.setdefault("USER", "app")
os.environ.setdefault("LOGNAME", "app")

import pymysql
from pymysql.cursors import DictCursor

# Same shape as main.COOKIE_VALUE. Illegal values must not be stored or echoed.
COOKIE_VALUE = re.compile(r"[A-Za-z0-9_-]{1,512}")
DEFAULT_ID = "default"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session (
  id          VARCHAR(64)  NOT NULL,
  auth_token  VARCHAR(255) NOT NULL,
  ct0         VARCHAR(512) NOT NULL,
  source      VARCHAR(64)  NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


class SessionStoreError(Exception):
    """MySQL is configured but the session cannot be read or written."""


@dataclass(frozen=True)
class StoredSession:
    auth_token: str
    ct0: str
    source: str | None = None


def database_url() -> str | None:
    value = (os.getenv("X_DATABASE_URL") or "").strip()
    return value or None


def _require_cookie(name: str, value: str) -> str:
    if not COOKIE_VALUE.fullmatch(value or ""):
        raise SessionStoreError(f"refusing to store malformed {name}")
    return value


def connect(url: str | None = None) -> Any:
    raw = url if url is not None else database_url()
    if not raw:
        raise SessionStoreError("X_DATABASE_URL is not set")
    parsed = urlparse(raw)
    if parsed.scheme != "mysql" or not parsed.hostname or not parsed.username:
        raise SessionStoreError("X_DATABASE_URL must be mysql://user:pass@host:port/db")
    database = parsed.path.lstrip("/")
    if not database or "/" in database:
        raise SessionStoreError("X_DATABASE_URL is missing a database name")
    try:
        return pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=unquote(parsed.username),
            password=unquote(parsed.password or ""),
            database=database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            cursorclass=DictCursor,
        )
    except pymysql.MySQLError as exc:
        raise SessionStoreError(
            f"MySQL connection failed ({exc.__class__.__name__})"
        ) from exc


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)


def load_session(conn: Any, session_id: str = DEFAULT_ID) -> StoredSession | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT auth_token, ct0, source FROM session WHERE id = %s",
            (session_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return StoredSession(row["auth_token"], row["ct0"], row.get("source"))


def upsert_session(
    conn: Any,
    auth_token: str,
    ct0: str,
    source: str,
    session_id: str = DEFAULT_ID,
) -> None:
    auth_token = _require_cookie("auth_token", auth_token)
    ct0 = _require_cookie("ct0", ct0)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO session (id, auth_token, ct0, source)
            VALUES (%s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
              auth_token = new.auth_token,
              ct0 = new.ct0,
              source = new.source
            """,
            (session_id, auth_token, ct0, source[:64]),
        )


def session_status(conn: Any, session_id: str = DEFAULT_ID) -> dict[str, Any] | None:
    """Lengths and source only. Never the cookie values."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source,
                   CHAR_LENGTH(auth_token) AS auth_token_len,
                   CHAR_LENGTH(ct0) AS ct0_len,
                   updated_at
            FROM session WHERE id = %s
            """,
            (session_id,),
        )
        return cur.fetchone()


def load_default() -> StoredSession | None:
    with connect() as conn:
        return load_session(conn)


def prepare_database(env_token: str | None = None, env_ct0: str | None = None) -> str:
    """Create the table. Seed one row from env cookies only when the table is empty.

    Returns a short status string safe to log.
    """
    with connect() as conn:
        ensure_schema(conn)
        existing = load_session(conn)
        if existing is not None:
            return "mysql session present"
        if env_token and env_ct0:
            upsert_session(conn, env_token, env_ct0, source="env-bootstrap")
            return "seeded empty mysql session from env"
        return "mysql session missing"
