"""Fold the earlier model settings into the server's model catalog, and report what can be used.

    python scripts/migrate_model_catalog.py            # migrate (idempotent) and print the catalog status
    python scripts/migrate_model_catalog.py --strip    # also blank the plaintext keys of data/model-profiles.json
                                                       # whose profile is already in the encrypted catalog

The API does the migration at start-up too; this script is for checking it by hand.  It never prints a key.
Run --strip only after a bot has answered with the migrated model: the browser's 'local file' profiles then
lose their keys (they stay listed, marked untested).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strip", action="store_true", help="blank migrated plaintext keys in data/model-profiles.json")
    args = parser.parse_args()
    from app.agent_chat_models import AgentModelEntry
    from app.agent_chat_store import ensure_conversation_archive_column
    from app.database import engine
    from app import agent_model_catalog as catalog
    AgentModelEntry.__table__.create(bind=engine, checkfirst=True)
    ensure_conversation_archive_column()
    added = catalog.migrate_legacy()
    print(f"migrated: {', '.join(added) if added else 'nothing new'}")
    if args.strip:
        print(f"blanked plaintext keys in the local profile file: {catalog.strip_local_profile_keys()}")
    for line in catalog.startup_report():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
