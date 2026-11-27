import os
import sqlite3
from pathlib import Path

from flask import current_app, g

SQL_DIR = Path(__file__).with_name("db")


def connect():
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if url and token:
        import libsql

        db = libsql.connect(database=url, auth_token=token)
    elif url or token:
        raise RuntimeError("Set both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
    else:
        path = Path(current_app.config["SQLITE_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)

    try:
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("Database foreign-key enforcement is required.")
    except Exception:
        db.close()
        raise
    return db


def init_db():
    db = connect()
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] > 0:
            return
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            for filename in ("schema.sql", "seed.sql"):
                sql = (SQL_DIR / filename).read_text(encoding="utf-8")
                for statement in sql.split(";"):
                    if statement.strip():
                        db.execute(statement)
            db.execute("PRAGMA user_version = 1")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def fetch_all(sql, args=()):
    cursor = get_db().execute(sql, tuple(args))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def write(sql, args):
    db = get_db()
    try:
        cursor = db.execute(sql, tuple(args))
        result = cursor.fetchone()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
