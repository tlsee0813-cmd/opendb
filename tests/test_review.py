import csv
import io
import re
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from app import create_app
from database import apply_sql_file


class ReviewTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "opendb.sqlite3"

    def create_client(self):
        return create_app({"TESTING": True, "SQLITE_PATH": self.path}).test_client()

    @staticmethod
    def csrf(response):
        return re.search(rb'name="_csrf" value="([^"]+)"', response.data).group(1).decode()

    def test_upgrade_preserves_existing_rows_and_imports_catalog_once(self):
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            apply_sql_file(db, "schema.sql")
            apply_sql_file(db, "seed.sql")
            apply_sql_file(db, "migrations/002_resource_types.sql")
            db.execute(
                "INSERT INTO resources (title, url, category_id, verification_id, usage_type_id) "
                "VALUES ('기존 자료', 'https://www.unicode.org/reports/tr15/', 4, 1, 2)"
            )
            before = db.execute("SELECT * FROM resources ORDER BY id").fetchall()

        client = self.create_client()
        self.assertIn("SQLite FTS5 전문 검색", client.get("/guide").text)
        self.assertIn("32개 주제", client.get("/topics").text)
        self.assertIn("자료 96건", client.get("/topics").text)
        topic_rows = client.get("/resources.csv", query_string={"q": "지도와 공간 데이터"})
        titles = [row["자료명"] for row in csv.DictReader(io.StringIO(topic_rows.data.decode("utf-8-sig")))]
        self.assertCountEqual(titles, ["GTFS Schedule 대중교통 명세", "pyproj 좌표 변환", "OGC STAC 공간 데이터 카탈로그"])
        self.create_client()
        with sqlite3.connect(self.path) as db:
            after = db.execute("SELECT * FROM resources ORDER BY id").fetchall()
            self.assertEqual(after[:len(before)], before)
            self.assertEqual(len(after) - len(before), 111)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM review_records").fetchone(), (127,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("DELETE FROM resources WHERE title = 'SQLite FTS5 전문 검색'")

        client = self.create_client()
        self.assertIn('href="https://www.sqlite.org/fts5.html"', client.get("/guide").text)
        self.assertIn("검색 결과가 없습니다.", client.get("/?q=FTS5+전문+검색").text)

    def test_records_can_be_searched_and_questions_can_be_resolved_and_reopened(self):
        client = self.create_client()
        new_resource = client.get("/resources/new")
        csrf = self.csrf(new_resource)
        created = client.post("/resources/new", data={
            "_csrf": csrf, "title": "시연용 자료", "url": "https://example.org/demo",
            "category_id": "4", "verification_id": "1", "usage_type_id": "2",
        })
        self.assertEqual(created.status_code, 303)
        resource = created.headers["Location"]
        new_record = resource + "/review/new"
        fields = {"_csrf": csrf, "summary": "기록만 검색되는 단서", "recorded_on": "2026-11-27"}

        self.assertEqual(client.post(new_record, data={**fields, "kind": "evidence"}).status_code, 422)
        self.assertEqual(client.post(new_record, data={
            **fields, "kind": "evidence", "source_url": "https://example.org/demo#evidence",
            "source_location": "테스트 절",
        }).status_code, 303)
        records_csv = client.get("/resources.csv", query_string={"q": fields["summary"]})
        rows = list(csv.DictReader(io.StringIO(records_csv.data.decode("utf-8-sig"))))
        self.assertEqual([row["자료명"] for row in rows], ["시연용 자료"])

        question = "기록을 완료한 뒤에도 조회되는가?"
        response = client.post(new_record, data={**fields, "kind": "question", "summary": question})
        self.assertEqual(response.status_code, 303)
        record_id = urlsplit(response.headers["Location"]).fragment.removeprefix("review-record-")
        self.assertTrue(record_id.isdigit())
        resolution = f"{resource}/review/{record_id}/resolution"
        self.assertIn(question, client.get("/review").text)
        self.assertEqual(client.post(resolution, data={"resolution": "done"}).status_code, 400)
        for state in ("done", "done", "open"):
            self.assertEqual(client.post(resolution, data={"_csrf": csrf, "resolution": state}).status_code, 303)
            self.assertEqual(question in client.get("/review").text, state == "open")
            self.assertIn(question, client.get(resource).text)

        self.assertIn("연결된 검토 기록 2건", client.get(resource + "/delete").text)
        self.assertEqual(client.post(resource + "/delete", data={"_csrf": csrf}).status_code, 303)
        self.assertNotIn(question, client.get("/review").text)
        self.assertEqual(client.get(resource).status_code, 404)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT id FROM review_records WHERE id = ?", (record_id,)).fetchone())
