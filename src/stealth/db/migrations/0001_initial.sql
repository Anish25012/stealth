-- One database per profile, so nothing here carries a profile id.

CREATE TABLE person (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'GB',
    date_of_birth TEXT,
    dob_opt_in INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE identifier (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('name', 'email', 'address', 'phone')),
    value TEXT NOT NULL,
    UNIQUE (kind, value)
);

CREATE TABLE consent (
    id INTEGER PRIMARY KEY,
    subject_name TEXT NOT NULL,
    given_by TEXT NOT NULL,
    method TEXT NOT NULL,
    wording_version TEXT NOT NULL,
    given_at TEXT NOT NULL
);

CREATE TABLE mail_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL,
    address TEXT NOT NULL,
    smtp_host TEXT,
    smtp_port INTEGER,
    smtp_security TEXT,
    imap_host TEXT,
    imap_port INTEGER,
    imap_security TEXT,
    username TEXT,
    is_own_mailbox INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE broker_contact_flag (
    broker_id TEXT NOT NULL,
    contact TEXT NOT NULL,
    reason TEXT NOT NULL,
    flagged_at TEXT NOT NULL,
    PRIMARY KEY (broker_id, contact)
);

CREATE TABLE cases (
    id INTEGER PRIMARY KEY,
    broker_id TEXT NOT NULL UNIQUE,
    broker_name TEXT NOT NULL,
    broker_domain TEXT NOT NULL,
    category TEXT NOT NULL,
    method TEXT NOT NULL,
    contact TEXT,
    tier TEXT NOT NULL,
    template TEXT NOT NULL,
    ref TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 0,
    relevant_time TEXT,
    deadline_on TEXT,
    complaint_on TEXT,
    last_request_on TEXT,
    next_action_on TEXT,
    custom_url TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX cases_status ON cases (status);

-- The audit log. Rows can only be added: see the triggers below.
CREATE TABLE event (
    id INTEGER PRIMARY KEY,
    case_id INTEGER REFERENCES cases (id),
    type TEXT NOT NULL,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX event_case ON event (case_id);

CREATE TRIGGER event_no_update BEFORE UPDATE ON event
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

CREATE TRIGGER event_no_delete BEFORE DELETE ON event
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

CREATE TABLE message (
    id INTEGER PRIMARY KEY,
    case_id INTEGER REFERENCES cases (id),
    direction TEXT NOT NULL CHECK (direction IN ('out', 'in')),
    kind TEXT NOT NULL,
    rfc_message_id TEXT UNIQUE,
    sender TEXT,
    subject TEXT,
    occurred_at TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    classification TEXT
);
CREATE INDEX message_case ON message (case_id);

CREATE TABLE artifact (
    id INTEGER PRIMARY KEY,
    case_id INTEGER REFERENCES cases (id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
