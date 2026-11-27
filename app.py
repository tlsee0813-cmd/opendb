import hmac
import os
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from database import close_db, fetch_all, init_db, write

PAGE_SIZE = 10
RESOURCE_SELECT = """
SELECT r.id, r.title, r.url, r.description, r.category_id, r.verification_id,
       r.checked_on, r.updated_at, c.name AS category, v.name AS verification
FROM resources AS r
JOIN categories AS c ON c.id = r.category_id
JOIN verification_levels AS v ON v.id = r.verification_id
"""


def options():
    return (
        fetch_all("SELECT id, name FROM categories ORDER BY id"),
        fetch_all("SELECT id, name FROM verification_levels ORDER BY id"),
    )


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


def validate_resource(form, categories, levels):
    fields = {
        key: form.get(key, "").strip()
        for key in ("title", "url", "description", "category_id", "verification_id", "checked_on")
    }
    errors = {}
    if not 1 <= len(fields["title"]) <= 100:
        errors["title"] = "자료명은 1~100자로 입력해 주세요."
    if len(fields["url"]) > 500 or not is_public_link(fields["url"]):
        errors["url"] = "http:// 또는 https://로 시작하는 올바른 주소를 입력해 주세요."
    if len(fields["description"]) > 1000:
        errors["description"] = "활용 메모는 1,000자까지 입력할 수 있습니다."
    if fields["category_id"] not in {str(item["id"]) for item in categories}:
        errors["category_id"] = "분류를 선택해 주세요."
    if fields["verification_id"] not in {str(item["id"]) for item in levels}:
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
        fetch_all("SELECT id FROM categories LIMIT 1")
        return jsonify(status="ok", database="turso" if cloud else "sqlite")

    @app.get("/")
    def catalog():
        query = request.args.get("q", "").strip()[:100]
        category_id = request.args.get("category", type=int)
        verification_id = request.args.get("verification", type=int)
        page = max(1, request.args.get("page", default=1, type=int))
        conditions = ["1 = 1"]
        args = []
        if query:
            conditions.append(
                "(instr(lower(r.title), lower(?)) > 0 OR instr(lower(r.description), lower(?)) > 0 "
                "OR instr(lower(r.url), lower(?)) > 0)"
            )
            args.extend([query] * 3)
        if category_id is not None:
            conditions.append("r.category_id = ?")
            args.append(category_id if 0 < category_id < 2**63 else 0)
        if verification_id is not None:
            conditions.append("r.verification_id = ?")
            args.append(verification_id if 0 < verification_id < 2**63 else 0)
        where = " WHERE " + " AND ".join(conditions)
        total = fetch_all("SELECT COUNT(*) AS n FROM resources AS r" + where, args)[0]["n"]
        last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, last_page)
        rows = fetch_all(
            RESOURCE_SELECT + where + " ORDER BY r.updated_at DESC, r.id DESC LIMIT ? OFFSET ?",
            [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        )
        stats = fetch_all(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT category_id) AS categories, "
            "COUNT(CASE WHEN verification_id IN (2, 3) THEN 1 END) AS verified FROM resources"
        )[0]
        categories, levels = options()
        return render_template(
            "catalog.html", rows=rows, query=query, category_id=category_id,
            verification_id=verification_id, categories=categories, levels=levels,
            total=total, stats=stats, page=page, last_page=last_page,
            offset=(page - 1) * PAGE_SIZE,
        )

    @app.get("/resources/<int:resource_id>")
    def detail(resource_id):
        return render_template("detail.html", resource=get_resource(resource_id))

    @app.route("/resources/new", methods=["GET", "POST"])
    @app.route("/resources/<int:resource_id>/edit", methods=["GET", "POST"])
    def resource_form(resource_id=None):
        resource = get_resource(resource_id) if resource_id is not None else None
        categories, levels = options()
        fields = resource or {"verification_id": 1}
        errors = {}
        if request.method == "POST":
            fields, errors = validate_resource(request.form, categories, levels)
            if not errors:
                args = (
                    fields["title"], fields["url"], fields["description"],
                    int(fields["category_id"]), int(fields["verification_id"]),
                    fields["checked_on"] or None,
                )
                try:
                    if resource is None:
                        changed = write(
                            "INSERT INTO resources (title, url, description, category_id, "
                            "verification_id, checked_on) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                            args,
                        )
                    else:
                        changed = write(
                            "UPDATE resources SET title=?, url=?, description=?, category_id=?, "
                            "verification_id=?, checked_on=?, updated_at=datetime('now') "
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
                    return redirect(url_for("detail", resource_id=changed[0]), code=303)
        return render_template(
            "form.html", resource=resource, fields=fields, errors=errors,
            categories=categories, levels=levels,
        ), 422 if errors else 200

    @app.route("/resources/<int:resource_id>/delete", methods=["GET", "POST"])
    def remove_resource(resource_id):
        if request.method == "POST":
            if write("DELETE FROM resources WHERE id = ? RETURNING id", (resource_id,)) is None:
                abort(404)
            flash("자료를 삭제했습니다.")
            return redirect(url_for("catalog"), code=303)
        return render_template("delete.html", resource=get_resource(resource_id))

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
