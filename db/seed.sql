INSERT INTO categories (id, name) VALUES
    (1, '데이터베이스'), (2, '배포·호스팅'), (3, '웹 프레임워크'), (4, '개발·협업');

INSERT INTO verification_levels (id, name) VALUES
    (1, '검토 예정'), (2, '문서 확인'), (3, '실행 확인');

INSERT INTO resources (title, url, description, category_id, verification_id, checked_on) VALUES
    ('OpenDB 참고 프로젝트', 'https://github.com/laadkke-arch/agentflow-open', '오픈소스 조사 시 출처, 용도, 제약 및 검증 수준을 기록하는 방식의 참고 사례.', 4, 2, '2026-11-27'),
    ('Git', 'https://git-scm.com/', '변경 이력을 관리하고 작업 버전을 추적하는 도구 후보.', 4, 1, NULL),
    ('GitHub Actions', 'https://docs.github.com/actions', '커밋 후 테스트를 자동 실행하는 방법을 검토할 자료.', 4, 1, NULL),
    ('Jinja', 'https://jinja.palletsprojects.com/', '서버에서 HTML을 생성할 때 템플릿 문법을 참고하는 자료.', 3, 1, NULL),
    ('HTMX', 'https://htmx.org/docs/', '전체 페이지를 다시 로드하지 않는 화면 전환 구현을 위한 조사 후보.', 3, 1, NULL),
    ('Streamlit Community Cloud', 'https://docs.streamlit.io/deploy/streamlit-community-cloud', 'Streamlit 기반 데이터 앱의 공개 배포 절차를 검토할 자료.', 2, 1, NULL),
    ('TiDB Cloud', 'https://docs.pingcap.com/tidbcloud/', 'MySQL 호환 클라우드 DB 옵션 비교 후보.', 1, 1, NULL),
    ('SQLite 외래키', 'https://www.sqlite.org/foreignkeys.html', '부모 행 참조와 외래키 활성화 정책을 살펴볼 수 있는 자료.', 1, 1, NULL),
    ('Playwright', 'https://playwright.dev/python/', '브라우저에서 검색과 CRUD 동작을 검증할 수 있는 도구.', 4, 1, NULL),
    ('Flask', 'https://flask.palletsprojects.com/', '폼 처리, 라우팅, 템플릿 렌더링을 담당하는 Python 웹 프레임워크.', 3, 1, NULL),
    ('Render', 'https://render.com/docs/free', 'GitHub 연결 웹서비스의 무료 배포 옵션과 디스크 제약을 확인한 공식 문서.', 2, 2, '2026-11-27'),
    ('Turso Python SDK', 'https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx', 'libSQL 원격 연결에 사용하는 Python 드라이버와 환경 변수 설정을 확인한 공식 문서.', 1, 2, '2026-11-27');
