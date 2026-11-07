-- Companies the person added themselves, and what the assisted scan found.

CREATE TABLE custom_company (
    id TEXT PRIMARY KEY,               -- "custom-<domain>", also the case's broker_id
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    email TEXT,                        -- chosen by the person; NULL while still being looked up
    policy_url TEXT,                   -- where the address was found, if Stealth found it
    candidates TEXT NOT NULL DEFAULT '[]',  -- JSON: what the lookup found, for the person to pick
    category TEXT NOT NULL DEFAULT 'other',
    listing_url TEXT,                  -- a page showing the person's details, if they gave one
    added_at TEXT NOT NULL
);

CREATE TABLE scan_result (
    id INTEGER PRIMARY KEY,
    site TEXT NOT NULL,                -- the domain the captured page was on
    broker_id TEXT,                    -- the company it belongs to, if Stealth knows it
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN ('pending', 'listed', 'not_listed')),
    artifact_id INTEGER REFERENCES artifact (id),
    captured_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX scan_result_broker ON scan_result (broker_id);
