-- Auto-send for companies the person adds: what Stealth decided, and why.
ALTER TABLE custom_company ADD COLUMN auto_status TEXT
    CHECK (auto_status IN ('sent', 'held'));     -- NULL: auto-send was off, or not yet judged
ALTER TABLE custom_company ADD COLUMN auto_reason TEXT;   -- why it was held, in plain English
ALTER TABLE custom_company ADD COLUMN auto_checks TEXT;   -- JSON: each check and its result
ALTER TABLE custom_company ADD COLUMN auto_page TEXT;     -- the page the address was found on
ALTER TABLE custom_company ADD COLUMN auto_at TEXT;
