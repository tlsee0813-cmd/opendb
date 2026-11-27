CREATE TABLE resource_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) BETWEEN 1 AND 40)
);

INSERT INTO resource_types (id, name) VALUES
    (1, '대체 서비스'), (2, '개발 부품'), (3, '참고 자료');

ALTER TABLE resources ADD COLUMN usage_type_id INTEGER
    REFERENCES resource_types(id) ON DELETE RESTRICT;

CREATE INDEX resources_by_usage_type ON resources(usage_type_id);

UPDATE resources SET usage_type_id = 1 WHERE url IN (
    'https://docs.streamlit.io/deploy/streamlit-community-cloud',
    'https://docs.pingcap.com/tidbcloud/',
    'https://render.com/docs/free'
);

UPDATE resources SET usage_type_id = 2 WHERE url IN (
    'https://git-scm.com/',
    'https://docs.github.com/actions',
    'https://jinja.palletsprojects.com/',
    'https://htmx.org/docs/',
    'https://playwright.dev/python/',
    'https://flask.palletsprojects.com/'
);

UPDATE resources SET usage_type_id = 3 WHERE url IN (
    'https://github.com/laadkke-arch/agentflow-open',
    'https://www.sqlite.org/foreignkeys.html',
    'https://github.com/tursodatabase/turso-docs/blob/main/sdk/python/quickstart.mdx'
);
