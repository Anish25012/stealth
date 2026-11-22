-- "Protect yourself": the checklist, reminders and alerts.

-- One row per checklist item the person has marked done or not for them.
CREATE TABLE protect_item (
    id TEXT PRIMARY KEY,               -- a checklist item id, such as "open-register"
    state TEXT NOT NULL CHECK (state IN ('done', 'not_for_me')),
    marked_at TEXT NOT NULL            -- ISO time
);

-- Something to come back to on a given day. Later steps (credit reports, Cifas) add their own.
CREATE TABLE reminder (
    topic TEXT PRIMARY KEY,            -- "protect:open-register", "credit:experian", ...
    title TEXT NOT NULL,               -- what the alert says when it's due
    message TEXT NOT NULL,
    link TEXT NOT NULL DEFAULT '',
    due_on TEXT NOT NULL,              -- a local calendar date, YYYY-MM-DD
    every_days INTEGER,                -- repeats this often once done; NULL for a one-off
    alerted_on TEXT,                   -- the due date an alert was last raised for
    created_at TEXT NOT NULL
);

-- What Stealth wants the person to know: shown in the app, and once in the tray.
CREATE TABLE alert (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    link TEXT NOT NULL DEFAULT '',     -- a page in Stealth, such as "/protect#open-register"
    created_at TEXT NOT NULL,
    notified_at TEXT,                  -- when the tray showed it, NULL until then
    dismissed_at TEXT
);
CREATE INDEX alert_open ON alert (dismissed_at);
