"""Session store tests. No network and no real MySQL."""

import pymysql
import pytest

import session_store
from session_store import SessionStoreError, StoredSession


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=None):
        self.conn.statements.append((sql, args))
        if sql.strip().upper().startswith("SELECT AUTH_TOKEN"):
            self.row = self.conn.row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.statements = []

    def cursor(self):
        return FakeCursor(self)


def test_upsert_rejects_malformed_cookie():
    with pytest.raises(SessionStoreError, match="malformed"):
        session_store.upsert_session(FakeConn(), "bad cookie", "abc", "test")


def test_upsert_and_load_round_trip_shape():
    conn = FakeConn()
    session_store.upsert_session(conn, "tok", "csrf", "matched-env")
    sql, args = conn.statements[-1]
    assert "VALUES (%s, %s, %s, %s)" in sql
    assert args == ("default", "tok", "csrf", "matched-env")
    conn.row = {"auth_token": "tok", "ct0": "csrf", "source": "matched-env"}
    loaded = session_store.load_session(conn)
    assert loaded == StoredSession("tok", "csrf", "matched-env")


def test_load_missing_row():
    assert session_store.load_session(FakeConn()) is None


def test_connect_error_does_not_include_password(monkeypatch):
    monkeypatch.setenv("X_DATABASE_URL", "mysql://user:s3cret@10.0.0.153:3306/mcp_x")

    def boom(**kwargs):
        raise pymysql.err.OperationalError(2003, "down")

    monkeypatch.setattr(session_store.pymysql, "connect", boom)
    with pytest.raises(SessionStoreError, match="MySQL connection failed") as exc:
        session_store.connect()
    assert "s3cret" not in str(exc.value)


def test_connect_rejects_non_mysql_url(monkeypatch):
    monkeypatch.setenv("X_DATABASE_URL", "file:/tmp/x.db")
    with pytest.raises(SessionStoreError, match="mysql://"):
        session_store.connect()
