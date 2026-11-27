CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) BETWEEN 1 AND 40)
);

CREATE TABLE IF NOT EXISTS verification_levels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) BETWEEN 1 AND 40)
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 100),
    url TEXT NOT NULL UNIQUE CHECK (length(url) BETWEEN 1 AND 500),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    verification_id INTEGER NOT NULL REFERENCES verification_levels(id) ON DELETE RESTRICT,
    checked_on TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS resources_by_category ON resources(category_id);
CREATE INDEX IF NOT EXISTS resources_by_verification ON resources(verification_id);
