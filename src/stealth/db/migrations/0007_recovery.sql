-- The recovery playbook's incident log. Like everything in the profile, it's in the encrypted
-- database: reference numbers and notes never leave this computer unless the person exports them.

CREATE TABLE incident (
    id INTEGER PRIMARY KEY,
    situation TEXT NOT NULL,           -- an id from stealth.protect.recovery.SITUATIONS
    started_on TEXT NOT NULL,          -- YYYY-MM-DD: when it happened or was noticed
    closed_on TEXT,                    -- YYYY-MM-DD, once the person says it's sorted
    created_at TEXT NOT NULL
);

-- One row per action the person has ticked or written about.
CREATE TABLE incident_action (
    incident_id INTEGER NOT NULL REFERENCES incident (id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    done_on TEXT,                      -- YYYY-MM-DD, or NULL if not done yet
    reference TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (incident_id, action_id)
);

-- The log: dated entries, some written by the person, some recorded as they tick actions off.
CREATE TABLE incident_entry (
    id INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incident (id) ON DELETE CASCADE,
    happened_on TEXT NOT NULL,         -- YYYY-MM-DD
    text TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX incident_entry_by_incident ON incident_entry (incident_id, happened_on);
