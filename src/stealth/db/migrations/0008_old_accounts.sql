-- Find my old accounts: companies found in the person's Gmail sign-up emails, and what they
-- chose to do about each. Only the company is kept: no subjects, bodies or message ids.

CREATE TABLE old_account (
    domain TEXT PRIMARY KEY,           -- the company's main domain, such as "spotify.com"
    name TEXT NOT NULL,
    first_seen_on TEXT NOT NULL,       -- YYYY-MM-DD: the oldest sign-up email found
    last_seen_on TEXT NOT NULL,
    found_at TEXT NOT NULL,            -- when a scan first found it
    decision TEXT CHECK (decision IN ('keep', 'delete')),  -- NULL until the person chooses
    decided_at TEXT,
    deletion_url TEXT,                 -- the company's own account-deletion page, if found
    deletion_checked_at TEXT           -- when Stealth looked for it
);

-- How far back the scan has read. One row.
CREATE TABLE old_account_scan (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scanned_at TEXT NOT NULL,
    oldest_on TEXT,                    -- YYYY-MM-DD: the oldest message reached so far
    more INTEGER NOT NULL DEFAULT 0    -- older matching messages are left to read
);
