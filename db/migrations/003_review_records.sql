CREATE TABLE review_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('evidence', 'limitation', 'question')),
    summary TEXT NOT NULL CHECK (length(trim(summary)) BETWEEN 1 AND 600),
    application TEXT CHECK (length(application) <= 500),
    source_url TEXT CHECK (length(source_url) BETWEEN 1 AND 500),
    source_location TEXT CHECK (length(source_location) <= 140),
    recorded_on TEXT NOT NULL,
    resolved_on TEXT,
    CHECK (kind <> 'evidence' OR source_url IS NOT NULL),
    CHECK (source_url IS NOT NULL OR source_location IS NULL),
    CHECK (kind = 'question' OR resolved_on IS NULL)
);

CREATE INDEX review_records_by_resource ON review_records(resource_id, recorded_on DESC, id DESC);
CREATE INDEX review_records_open_questions ON review_records(recorded_on DESC, id DESC)
    WHERE kind = 'question' AND resolved_on IS NULL;

WITH examples (resource_url, kind, summary, application, source_url, source_location) AS (
    VALUES
        (
            'https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx',
            'evidence',
            'Turso Cloud의 libSQL 엔진은 Python libsql 드라이버로 원격 접속할 수 있다.',
            'Render에서 Turso의 데이터베이스 주소와 전용 토큰으로 연결한다.',
            'https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx#remote-libsql-database-libsql',
            'Remote libSQL database'
        ),
        (
            'https://render.com/docs/free',
            'limitation',
            'Render 무료 웹서비스의 로컬 파일 변경은 재배포나 인스턴스 재시작 때 사라질 수 있다.',
            '공개 앱에서 쓰는 자료는 Turso에 저장한다.',
            'https://render.com/docs/free#local-files-lost-on-redeploy',
            'Local files lost on redeploy'
        ),
        (
            'https://render.com/docs/free',
            'evidence',
            'Render는 웹서비스에 공개 접속 주소를 제공하며 Flask 같은 동적 웹 앱을 실행할 수 있다.',
            'GitHub에 올린 앱을 연결해 제출용 공개 URL을 제공한다.',
            'https://render.com/docs/web-services',
            'Web Services'
        ),
        (
            'https://www.sqlite.org/foreignkeys.html',
            'evidence',
            'SQLite의 외래키 집행은 데이터베이스 연결마다 활성화할 수 있다.',
            'DB 연결을 만들 때 PRAGMA foreign_keys를 활성화하고 적용 여부를 확인한다.',
            'https://www.sqlite.org/foreignkeys.html#fk_enable',
            'Enabling Foreign Key Support'
        ),
        (
            'https://github.com/laadkke-arch/agentflow-open',
            'evidence',
            '공개 조사 지침은 출처와 확인일, 활용 제약, 검증 수준을 함께 기록한다.',
            '확인한 사실과 OpenDB에 적용할 판단을 각각 별도 입력으로 남긴다.',
            'https://github.com/laadkke-arch/agentflow-open/blob/main/README.md#자료-관리',
            '자료 관리'
        ),
        (
            'https://docs.pingcap.com/tidbcloud/',
            'question',
            'TiDB Cloud에서 같은 자료 관계를 표현할 때 날짜 기본값과 외래키 처리가 어떻게 다른가?',
            '작은 재현 데이터를 준비해 등록, 삭제, 무결성 결과를 비교한다.',
            NULL,
            NULL
        ),
        (
            'https://htmx.org/docs/',
            'question',
            '검색 결과만 갱신할 때 주소창의 검색 조건과 브라우저 뒤로가기를 함께 유지할 수 있는가?',
            '부분 갱신을 적용한 뒤 두 가지 브라우저 동작을 점검한다.',
            NULL,
            NULL
        ),
        (
            'https://docs.streamlit.io/deploy/streamlit-community-cloud',
            'question',
            '같은 자료 등록·수정 흐름을 Streamlit에서 구현할 때 어떤 화면 전환이 필요한가?',
            NULL,
            NULL,
            NULL
        )
)
INSERT INTO review_records (resource_id, kind, summary, application, source_url, source_location, recorded_on)
SELECT r.id, e.kind, e.summary, e.application, e.source_url, e.source_location, '2026-11-27'
FROM examples AS e JOIN resources AS r ON r.url = e.resource_url;
