import csv
import hmac
import io
import os
import secrets
from datetime import date, datetime
from pathlib import Path
from unicodedata import normalize
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from database import CURATED_CATALOG, TOPIC_CATALOG, close_db, fetch_all, init_db, write

PAGE_SIZE = 10
REVIEW_PAGE_SIZE = 8
RECORD_TYPES = {"evidence": "근거", "limitation": "제약", "question": "확인할 점"}
CATALOG_ORDER = {
    "recent": ("최근 수정순", "r.updated_at DESC, r.id DESC"),
    "title": ("자료명순", "r.title COLLATE NOCASE, r.id"),
    "checked": ("최근 확인순", "r.checked_on DESC, r.id DESC"),
}
RESOURCE_SELECT = """
SELECT r.id, r.title, r.url, r.description, r.category_id, r.verification_id,
       r.usage_type_id, r.checked_on, r.updated_at, c.name AS category,
       v.name AS verification, COALESCE(t.name, '미지정') AS usage_type
FROM resources AS r
JOIN categories AS c ON c.id = r.category_id
JOIN verification_levels AS v ON v.id = r.verification_id
LEFT JOIN resource_types AS t ON t.id = r.usage_type_id
"""
REVIEW_BOARD_SELECT = """
SELECT rr.id, rr.resource_id, rr.kind, rr.summary, rr.recorded_on,
       r.title, c.name AS category
FROM review_records AS rr
JOIN resources AS r ON r.id = rr.resource_id
JOIN categories AS c ON c.id = r.category_id
"""


def category_options(categories):
    by_name = {item["name"]: item for item in categories}
    groups = []
    for group in TOPIC_CATALOG["groups"]:
        items = [by_name.pop(topic["name"]) for topic in group["topics"] if topic["name"] in by_name]
        if items:
            groups.append({"title": group["title"], "items": items})
    if by_name:
        groups.insert(0, {"title": "기본 분류", "items": list(by_name.values())})
    return groups


def options():
    categories = fetch_all("SELECT id, name FROM categories ORDER BY id")
    return {
        "categories": categories,
        "category_groups": category_options(categories),
        "levels": fetch_all("SELECT id, name FROM verification_levels ORDER BY id"),
        "types": fetch_all("SELECT id, name FROM resource_types ORDER BY id"),
    }


def catalog_parameters():
    sort = request.args.get("sort", "recent")
    page = request.args.get("page", default=1, type=int)
    return {
        "q": request.args.get("q", "").strip()[:100] or None,
        "category": request.args.get("category", type=int),
        "usage": request.args.get("usage", type=int),
        "verification": request.args.get("verification", type=int),
        "sort": sort if sort in CATALOG_ORDER and sort != "recent" else None,
        "page": page if page > 1 else None,
    }


def catalog_where(parameters):
    conditions = []
    args = []
    if parameters["q"]:
        conditions.append(
            "(instr(lower(r.title), lower(?)) > 0 OR instr(lower(r.description), lower(?)) > 0 "
            "OR instr(lower(r.url), lower(?)) > 0 OR EXISTS ("
            "SELECT 1 FROM categories AS category WHERE category.id = r.category_id "
            "AND instr(lower(category.name), lower(?)) > 0) OR EXISTS ("
            "SELECT 1 FROM review_records AS rr WHERE rr.resource_id = r.id "
            "AND (instr(lower(rr.summary), lower(?)) > 0 "
            "OR instr(lower(rr.application), lower(?)) > 0)))"
        )
        args.extend([parameters["q"]] * 6)
    for name, column in (("category", "r.category_id"), ("verification", "r.verification_id"),
                         ("usage", "r.usage_type_id")):
        value = parameters[name]
        if value is None:
            continue
        if name == "usage" and value == 0:
            conditions.append(f"{column} IS NULL")
        else:
            conditions.append(f"{column} = ?")
            args.append(value if 0 < value < 2**63 else 0)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, args


def csv_cell(value):
    # Keep text beginning with a spreadsheet formula marker as text on import.
    text = "" if value is None else str(value)
    if text.startswith(("\t", "\r", "\n")) or normalize("NFKC", text).lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def get_resource(resource_id):
    if not 0 < resource_id < 2**63:
        abort(404)
    resources = fetch_all(RESOURCE_SELECT + " WHERE r.id = ?", (resource_id,))
    if not resources:
        abort(404)
    return resources[0]


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def is_public_link(url):
    try:
        parsed = urlsplit(url)
        _ = parsed.port
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() or ord(char) < 32 for char in url)
            and "\\" not in url
        )
    except ValueError:
        return False


def is_iso_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def today_in_seoul():
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def validate_review(form):
    fields = {
        key: form.get(key, "").strip()
        for key in ("kind", "summary", "application", "source_url", "source_location", "recorded_on")
    }
    errors = {}
    if fields["kind"] not in RECORD_TYPES:
        errors["kind"] = "기록 종류를 선택해 주세요."
    if not 1 <= len(fields["summary"]) <= 600:
        errors["summary"] = "확인한 내용이나 질문을 1~600자로 입력해 주세요."
    if len(fields["application"]) > 500:
        errors["application"] = "적용 판단은 500자까지 입력할 수 있습니다."
    url = fields["source_url"]
    if not url and fields["kind"] == "evidence":
        errors["source_url"] = "근거의 원문 주소를 입력해 주세요."
    elif url and (len(url) > 500 or not is_public_link(url)):
        errors["source_url"] = "http:// 또는 https://로 시작하는 올바른 주소를 입력해 주세요."
    if len(fields["source_location"]) > 140:
        errors["source_location"] = "원문 위치는 140자까지 입력할 수 있습니다."
    elif fields["source_location"] and not url:
        errors["source_location"] = "원문 위치를 입력하려면 주소를 함께 입력해 주세요."
    if not is_iso_date(fields["recorded_on"]):
        errors["recorded_on"] = "기록일을 입력해 주세요."
    return fields, errors


def validate_resource(form, choices):
    fields = {
        key: form.get(key, "").strip()
        for key in ("title", "url", "description", "category_id", "usage_type_id", "verification_id", "checked_on")
    }
    errors = {}
    if not 1 <= len(fields["title"]) <= 100:
        errors["title"] = "자료명은 1~100자로 입력해 주세요."
    if len(fields["url"]) > 500 or not is_public_link(fields["url"]):
        errors["url"] = "http:// 또는 https://로 시작하는 올바른 주소를 입력해 주세요."
    if len(fields["description"]) > 1000:
        errors["description"] = "활용 메모는 1,000자까지 입력할 수 있습니다."
    if fields["category_id"] not in {str(item["id"]) for item in choices["categories"]}:
        errors["category_id"] = "분류를 선택해 주세요."
    if fields["usage_type_id"] and fields["usage_type_id"] not in {str(item["id"]) for item in choices["types"]}:
        errors["usage_type_id"] = "목록에서 활용 유형을 선택해 주세요."
    if fields["verification_id"] not in {str(item["id"]) for item in choices["levels"]}:
        errors["verification_id"] = "검증 수준을 선택해 주세요."
    checked_on = fields["checked_on"]
    if checked_on:
        if not is_iso_date(checked_on):
            errors["checked_on"] = "확인일을 YYYY-MM-DD 형식으로 입력해 주세요."
    elif fields["verification_id"] in {"2", "3"}:
        errors["checked_on"] = "문서 확인 또는 실행 확인에는 확인일이 필요합니다."
    return fields, errors


def create_app(test_config=None):
    app = Flask(__name__)
    hosted = os.environ.get("OPENDB_HOSTED") == "1"
    cloud = bool(os.environ.get("TURSO_DATABASE_URL", "").strip())
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        SQLITE_PATH=os.environ.get("SQLITE_PATH", str(Path(app.instance_path) / "opendb.sqlite3")),
        SESSION_COOKIE_SECURE=hosted,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=16_384,
    )
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    app.jinja_env.globals.update(csrf_token=csrf_token)

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            expected = session.get("_csrf")
            submitted = request.form.get("_csrf", "")
            if not expected or not hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8")):
                abort(400, description="요청이 만료되었습니다. 양식을 다시 열어 주세요.")

    @app.context_processor
    def shared_labels():
        backend = "Turso Cloud" if cloud else "SQLite"
        return dict(backend=backend, temporary=hosted and not cloud)

    @app.get("/healthz")
    def health():
        fetch_all("SELECT id FROM review_records LIMIT 1")
        return jsonify(status="ok", database="turso" if cloud else "sqlite")

    @app.get("/")
    def catalog():
        parameters = catalog_parameters()
        where, args = catalog_where(parameters)
        sort = parameters["sort"] or "recent"
        order = CATALOG_ORDER[sort][1]
        total = fetch_all("SELECT COUNT(*) AS n FROM resources AS r" + where, args)[0]["n"]
        last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(parameters.pop("page") or 1, last_page)
        rows = fetch_all(
            RESOURCE_SELECT + where + f" ORDER BY {order} LIMIT ? OFFSET ?",
            [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        )
        stats = fetch_all(
            "SELECT COUNT(*) AS total, COUNT(CASE WHEN verification_id IN (2, 3) THEN 1 END) AS verified, "
            "(SELECT COUNT(*) FROM review_records WHERE kind='question' AND resolved_on IS NULL) "
            "AS open_questions FROM resources"
        )[0]
        return render_template(
            "catalog.html", rows=rows, parameters=parameters, sort=sort, orders=CATALOG_ORDER,
            return_parameters={**parameters, "page": page if page > 1 else None},
            total=total, stats=stats, page=page, last_page=last_page,
            **options(),
        )

    @app.get("/resources.csv")
    def export_catalog():
        parameters = catalog_parameters()
        where, args = catalog_where(parameters)
        order = CATALOG_ORDER[parameters["sort"] or "recent"][1]
        rows = fetch_all(RESOURCE_SELECT + where + f" ORDER BY {order}", args)
        csv_file = io.StringIO(newline="")
        writer = csv.writer(csv_file)
        writer.writerow(("자료명", "출처 URL", "분류", "활용 유형", "검증 수준", "확인일", "활용 메모"))
        for row in rows:
            writer.writerow(csv_cell(row[name]) for name in (
                "title", "url", "category", "usage_type", "verification", "checked_on", "description",
            ))
        return Response(
            csv_file.getvalue().encode("utf-8-sig"),
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="opendb-resources.csv"'},
        )

    @app.get("/resources/<int:resource_id>")
    def detail(resource_id):
        resource = get_resource(resource_id)
        records = fetch_all(
            "SELECT id, kind, summary, application, source_url, source_location, recorded_on, resolved_on "
            "FROM review_records WHERE resource_id = ? ORDER BY recorded_on DESC, id DESC", (resource_id,),
        )
        return render_template("detail.html", resource=resource, parameters=catalog_parameters(),
                               records=records, record_types=RECORD_TYPES)

    @app.get("/review")
    def review_board():
        counts = fetch_all(
            "SELECT COUNT(CASE WHEN kind='evidence' THEN 1 END) AS evidence, "
            "COUNT(CASE WHEN kind='limitation' THEN 1 END) AS limitations, "
            "COUNT(CASE WHEN kind='question' AND resolved_on IS NULL THEN 1 END) AS open_questions "
            "FROM review_records"
        )[0]
        last_page = max(1, (counts["open_questions"] + REVIEW_PAGE_SIZE - 1) // REVIEW_PAGE_SIZE)
        page = min(max(1, request.args.get("page", default=1, type=int)), last_page)
        pending = fetch_all(
            REVIEW_BOARD_SELECT + " WHERE rr.kind='question' AND rr.resolved_on IS NULL "
            "ORDER BY rr.recorded_on DESC, rr.id DESC LIMIT ? OFFSET ?",
            (REVIEW_PAGE_SIZE, (page - 1) * REVIEW_PAGE_SIZE),
        )
        latest = fetch_all(
            REVIEW_BOARD_SELECT + " WHERE rr.kind IN ('evidence','limitation') "
            "ORDER BY rr.recorded_on DESC, rr.id DESC LIMIT 6"
        )
        levels = fetch_all(
            "SELECT v.id, v.name, COUNT(r.id) AS total FROM verification_levels AS v "
            "LEFT JOIN resources AS r ON r.verification_id = v.id GROUP BY v.id, v.name ORDER BY v.id"
        )
        total = sum(level["total"] for level in levels)
        return render_template("review_board.html", counts=counts, pending=pending, latest=latest,
                               levels=levels, total=total, page=page, last_page=last_page,
                               record_types=RECORD_TYPES)

    @app.get("/guide")
    def guide():
        guides = CURATED_CATALOG["guides"]
        urls = [item["url"] for group in guides for item in group["resources"]]
        placeholders = ",".join("?" for _ in urls)
        linked = fetch_all(f"SELECT id, url FROM resources WHERE url IN ({placeholders})", urls)
        resource_ids = {row["url"]: row["id"] for row in linked}
        return render_template("guide.html", guides=guides, resource_ids=resource_ids)

    @app.get("/topics")
    def topics():
        counts = fetch_all(
            "SELECT c.id, c.name, COUNT(r.id) AS total FROM categories AS c "
            "LEFT JOIN resources AS r ON r.category_id = c.id GROUP BY c.id, c.name"
        )
        by_name = {entry["name"]: entry for entry in counts}
        groups = []
        for group in TOPIC_CATALOG["groups"]:
            topics = [{**topic, **by_name[topic["name"]]} for topic in group["topics"]]
            groups.append({"title": group["title"], "description": group["description"], "topics": topics})
        listed_topics = [topic for group in groups for topic in group["topics"]]
        return render_template("topics.html", groups=groups, topic_count=len(listed_topics),
                               resource_count=sum(topic["total"] for topic in listed_topics))

    @app.route("/resources/<int:resource_id>/review/new", methods=["GET", "POST"])
    def review_form(resource_id):
        resource = get_resource(resource_id)
        parameters = catalog_parameters()
        fields = {"kind": "evidence", "recorded_on": today_in_seoul()}
        errors = {}
        if request.method == "POST":
            fields, errors = validate_review(request.form)
            if not errors:
                saved = write(
                    "INSERT INTO review_records (resource_id, kind, summary, application, "
                    "source_url, source_location, recorded_on) "
                    "SELECT id, ?, ?, ?, ?, ?, ? FROM resources WHERE id = ? RETURNING id",
                    (fields["kind"], fields["summary"], fields["application"] or None,
                     fields["source_url"] or None, fields["source_location"] or None,
                     fields["recorded_on"], resource_id),
                )
                if saved is None:
                    abort(404)
                flash("검토 기록을 추가했습니다.")
                return redirect(url_for("detail", resource_id=resource_id,
                                        _anchor=f"review-record-{saved[0]}", **parameters), code=303)
        return render_template("review_form.html", resource=resource, fields=fields, errors=errors,
                               parameters=parameters, record_types=RECORD_TYPES), 422 if errors else 200

    @app.post("/resources/<int:resource_id>/review/<int:record_id>/resolution")
    def review_resolution(resource_id, record_id):
        if not 0 < resource_id < 2**63 or not 0 < record_id < 2**63:
            abort(404)
        action = request.form.get("resolution")
        if action not in ("done", "open"):
            abort(400, description="확인 상태를 선택해 주세요.")
        resolved_on = today_in_seoul() if action == "done" else None
        saved = write(
            "UPDATE review_records SET resolved_on = CASE WHEN ? IS NULL THEN NULL "
            "ELSE COALESCE(resolved_on, ?) END "
            "WHERE id = ? AND resource_id = ? AND kind = 'question' RETURNING id",
            (resolved_on, resolved_on, record_id, resource_id),
        )
        if saved is None:
            abort(404)
        flash("확인할 점을 완료했습니다." if resolved_on else "확인할 점을 다시 열었습니다.")
        return redirect(url_for("detail", resource_id=resource_id, _anchor=f"review-record-{record_id}",
                                **catalog_parameters()), code=303)

    @app.route("/resources/new", methods=["GET", "POST"])
    @app.route("/resources/<int:resource_id>/edit", methods=["GET", "POST"])
    def resource_form(resource_id=None):
        resource = get_resource(resource_id) if resource_id is not None else None
        choices = options()
        parameters = catalog_parameters()
        fields = resource or {"verification_id": 1}
        errors = {}
        if request.method == "POST":
            fields, errors = validate_resource(request.form, choices)
            if not errors:
                args = (
                    fields["title"], fields["url"], fields["description"],
                    int(fields["category_id"]), int(fields["verification_id"]),
                    fields["checked_on"] or None, int(fields["usage_type_id"]) if fields["usage_type_id"] else None,
                )
                try:
                    if resource is None:
                        changed = write(
                            "INSERT INTO resources (title, url, description, category_id, "
                            "verification_id, checked_on, usage_type_id) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                            args,
                        )
                    else:
                        changed = write(
                            "UPDATE resources SET title=?, url=?, description=?, category_id=?, "
                            "verification_id=?, checked_on=?, usage_type_id=?, updated_at=datetime('now') "
                            "WHERE id=? RETURNING id",
                            (*args, resource_id),
                        )
                except Exception as error:
                    if "UNIQUE constraint failed: resources.url" not in str(error):
                        raise
                    errors["url"] = "이미 등록된 주소입니다."
                else:
                    if changed is None:
                        abort(404)
                    flash("자료를 수정했습니다." if resource else "자료를 추가했습니다.")
                    return redirect(url_for("detail", resource_id=changed[0], **parameters), code=303)
        return render_template(
            "form.html", resource=resource, fields=fields, errors=errors,
            parameters=parameters, **choices,
        ), 422 if errors else 200

    @app.route("/resources/<int:resource_id>/delete", methods=["GET", "POST"])
    def remove_resource(resource_id):
        parameters = catalog_parameters()
        if request.method == "POST":
            if not 0 < resource_id < 2**63:
                abort(404)
            if write("DELETE FROM resources WHERE id = ? RETURNING id", (resource_id,)) is None:
                abort(404)
            flash("자료를 삭제했습니다.")
            return redirect(url_for("catalog", **parameters), code=303)
        resource = get_resource(resource_id)
        count = fetch_all("SELECT COUNT(*) AS total FROM review_records WHERE resource_id = ?", (resource_id,))[0]["total"]
        return render_template("delete.html", resource=resource, parameters=parameters, record_count=count)

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error_page(error):
        messages = {
            400: error.description,
            404: "자료가 삭제되었거나 주소가 변경되었습니다.",
            413: "입력 데이터가 너무 큽니다. 내용을 줄여 다시 시도해 주세요.",
            500: "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        }
        return render_template("error.html", status=error.code, message=messages[error.code]), error.code

    with app.app_context():
        init_db()
    return app
