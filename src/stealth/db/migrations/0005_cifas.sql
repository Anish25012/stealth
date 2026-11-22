-- Cifas Protective Registration: when the person's registration started and when it ends.
-- One row at most: a person has one registration at a time. Its reminders live in the reminder
-- table, under "cifas:".

CREATE TABLE cifas_registration (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_on TEXT NOT NULL,          -- a local calendar date, YYYY-MM-DD
    ends_on TEXT NOT NULL,             -- 24 months later
    recorded_at TEXT NOT NULL          -- ISO time
);
