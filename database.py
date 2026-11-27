import json
import os
import sqlite3
from pathlib import Path

from flask import current_app, g

SQL_DIR = Path(__file__).with_name("db")
CURATED_CATALOG = json.loads((SQL_DIR / "curated_catalog.json").read_text(encoding="utf-8"))
TOPIC_CATALOG = json.loads((SQL_DIR / "topic_catalog.json").read_text(encoding="utf-8"))
CURATED_INDEX = "resources_by_updated"
TOPIC_INDEX = "resources_by_category_updated"


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


def insert_rows(db, statement, rows, suffix=""):
    placeholders = "(" + ",".join("?" for _ in rows[0]) + ")"
    values = ",".join(placeholders for _ in rows)
    return db.execute(statement + " VALUES " + values + suffix,
                      [value for row in rows for value in row])


def import_readings(db, entries, checked_on):
    rows = [
        (item["title"], item["url"], item["description"], category_id, item["usage"], 2, checked_on)
        for item, category_id in entries
    ]
    inserted = dict(insert_rows(
        db, "INSERT INTO resources (title, url, description, category_id, usage_type_id, verification_id, checked_on)",
        rows, " ON CONFLICT(url) DO NOTHING RETURNING url, id",
    ).fetchall() or [])
    notes = []
    for item, _ in entries:
        resource_id = inserted.get(item["url"])
        if resource_id is None:
            continue
        for kind in ("evidence", "limitation", "question"):
            if kind in item:
                notes.append((resource_id, kind, item[kind], item["application"] if kind == "evidence" else None,
                              item["url"] if kind != "question" else None,
                              item.get("location") if kind != "question" else None, checked_on))
    if notes:
        insert_rows(db, "INSERT INTO review_records (resource_id, kind, summary, application, source_url, "
                        "source_location, recorded_on)", notes)


def import_curated_catalog(db):
    entries = [(item, item["category"]) for guide in CURATED_CATALOG["guides"] for item in guide["resources"]]
    import_readings(db, entries, CURATED_CATALOG["checked_on"])
    db.execute("CREATE INDEX resources_by_updated ON resources(updated_at DESC, id DESC)")


def import_topic_catalog(db):
    topics = [topic for group in TOPIC_CATALOG["groups"] for topic in group["topics"]]
    insert_rows(db, "INSERT INTO categories (name)", [(topic["name"],) for topic in topics],
                " ON CONFLICT(name) DO NOTHING")
    category_ids = dict(db.execute("SELECT name, id FROM categories").fetchall())
    entries = [(item, category_ids[topic["name"]]) for topic in topics for item in topic["resources"]]
    import_readings(db, entries, TOPIC_CATALOG["checked_on"])
    db.execute("CREATE INDEX resources_by_category_updated ON resources(category_id, updated_at DESC, id DESC)")


def init_db():
    db = connect()
    try:
        if schema_has(db, TOPIC_INDEX, "index"):
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
        if not schema_has(db, TOPIC_INDEX, "index"):
            import_topic_catalog(db)
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
