-- Data breaches the person's email addresses were found in, from Have I Been Pwned or added by
-- hand. Settings (on or off, how often, last check) live in the setting table; the HIBP API key
-- lives in the keychain, never here.

CREATE TABLE breach (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,               -- which of the person's addresses, lower case
    name TEXT NOT NULL,                -- HIBP's breach name, or "manual-..." for one added by hand
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    breach_date TEXT,                  -- YYYY-MM-DD, if known
    data_classes TEXT NOT NULL DEFAULT '[]',  -- JSON: what was exposed, such as "Passwords"
    description TEXT NOT NULL DEFAULT '',     -- plain text, never HTML
    verified INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL CHECK (source IN ('hibp', 'manual', 'demo')),
    found_at TEXT NOT NULL,            -- ISO time Stealth first recorded it
    dealt_with_at TEXT,                -- when the person said they'd dealt with it
    UNIQUE (email, name)
);
