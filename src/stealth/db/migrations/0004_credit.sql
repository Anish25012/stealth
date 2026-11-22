-- Credit report reminders: when the person last checked each agency's report, and their notes.
-- The reminders themselves live in the reminder table, under "credit:<agency>".

CREATE TABLE credit_check (
    agency TEXT PRIMARY KEY,           -- "experian", "equifax" or "transunion"
    last_checked_on TEXT,              -- a local calendar date, YYYY-MM-DD, or NULL
    note TEXT NOT NULL DEFAULT '',     -- anything that looked wrong, in the person's words
    note_updated_at TEXT
);
