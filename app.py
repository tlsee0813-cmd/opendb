import csv
import hmac
import io
import os
import secrets
from datetime import date
from pathlib import Path
from unicodedata import normalize
from urllib.parse import urlsplit

from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from database import close_db, fetch_all, init_db, write

PAGE_SIZE = 10
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


def options():
    return {
        "categories": fetch_all("SELECT id, name FROM categories ORDER BY id"),
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
            "OR instr(lower(r.url), lower(?)) > 0)"
        )
        args.extend([parameters["q"]] * 3)
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
        try:
            if date.fromisoformat(checked_on).isoformat() != checked_on:
                raise ValueError
        except ValueError:
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
        fetch_all("SELECT id FROM resource_types LIMIT 1")
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
            "SELECT COUNT(*) AS total, COUNT(DISTINCT category_id) AS categories, "
            "COUNT(CASE WHEN verification_id IN (2, 3) THEN 1 END) AS verified FROM resources"
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
        return render_template("detail.html", resource=get_resource(resource_id), parameters=catalog_parameters())

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
            if write("DELETE FROM resources WHERE id = ? RETURNING id", (resource_id,)) is None:
                abort(404)
            flash("자료를 삭제했습니다.")
            return redirect(url_for("catalog", **parameters), code=303)
        return render_template("delete.html", resource=get_resource(resource_id), parameters=parameters)

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
