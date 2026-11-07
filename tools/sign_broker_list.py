"""Sign the broker list for publishing. Runs in CI with the private key in BROKER_LIST_KEY.

    python tools/sign_broker_list.py OUT_DIR

Writes OUT_DIR/brokers.json (the exact bytes from the package) and OUT_DIR/brokers.json.sig,
then checks the signature against the public key built into the app, so a mismatched secret
fails the job instead of publishing a list every copy of Stealth would refuse.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from stealth.registry import signing
from stealth.registry.loader import packaged_document, validate

SOURCE = Path(__file__).resolve().parents[1] / "src/stealth/registry/data/brokers.json"


def main() -> int:
    key = os.environ.get("BROKER_LIST_KEY", "")
    if not key:
        print("BROKER_LIST_KEY isn't set.", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    data = SOURCE.read_bytes()
    validate(packaged_document(), "the repository")
    signature = signing.sign(data, key)
    if not signing.verify(data, signature):
        print("The key doesn't match the public key in signing.py.", file=sys.stderr)
        return 1
    (out / "brokers.json").write_bytes(data)
    (out / "brokers.json.sig").write_text(signature + "\n", "ascii")
    print(f"Signed broker list version {packaged_document()['version']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
