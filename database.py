import json
import os
import sqlite3
from pathlib import Path

from flask import current_app, g

SQL_DIR = Path(__file__).with_name("db")
CURATED_CATALOG = json.loads((SQL_DIR / "curated_catalog.json").read_text(encoding="utf-8"))
CURATED_INDEX = "resources_by_updated"


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


def schema_has(db, name, kind="table"):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
    ).fetchone() is not None


def apply_sql_file(db, filename):
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            db.execute(statement)


def import_curated_catalog(db):
    checked_on = CURATED_CATALOG["checked_on"]
    for guide in CURATED_CATALOG["guides"]:
        for resource in guide["resources"]:
            inserted = db.execute(
                "INSERT INTO resources (title, url, description, category_id, usage_type_id, "
                "verification_id, checked_on) VALUES (?, ?, ?, ?, ?, 2, ?) "
                "ON CONFLICT(url) DO NOTHING RETURNING id",
                (resource["title"], resource["url"], resource["description"], resource["category"],
                 resource["usage"], checked_on),
            ).fetchall()
            if not inserted:
                continue
            resource_id = inserted[0][0]
            for kind in ("evidence", "limitation", "question"):
                if kind not in resource:
                    continue
                db.execute(
                    "INSERT INTO review_records (resource_id, kind, summary, application, source_url, "
                    "source_location, recorded_on) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (resource_id, kind, resource[kind], resource["application"] if kind == "evidence" else None,
                     resource["url"] if kind != "question" else None,
                     resource["location"] if kind != "question" else None, checked_on),
                )
    db.execute("CREATE INDEX resources_by_updated ON resources(updated_at DESC, id DESC)")


def init_db():
    db = connect()
    try:
        if schema_has(db, CURATED_INDEX, "index"):
            return
        db.execute("BEGIN IMMEDIATE")
        if not schema_has(db, "resources"):
            apply_sql_file(db, "schema.sql")
            apply_sql_file(db, "seed.sql")
        if not schema_has(db, "resource_types"):
            apply_sql_file(db, "migrations/002_resource_types.sql")
        if not schema_has(db, "review_records"):
            apply_sql_file(db, "migrations/003_review_records.sql")
        # Create the listing index only after the catalog is imported in the same transaction.
        if not schema_has(db, CURATED_INDEX, "index"):
            import_curated_catalog(db)
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
        rows = db.execute(sql, tuple(args)).fetchall()
        db.commit()
        return rows[0] if rows else None
    except Exception:
        db.rollback()
        raise
