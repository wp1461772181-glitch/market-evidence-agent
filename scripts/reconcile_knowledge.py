#!/usr/bin/env python3
"""Safely verify/requeue ready chunks in the selected test_* collection."""
import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy.engine import make_url

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repair', action='store_true', help='requeue missing/mismatched vectors; never deletes collections')
args = parser.parse_args()
url = make_url(os.environ.get('DATABASE_URL', ''))
if url.get_backend_name() != 'postgresql' or not url.database or not url.database.startswith('test_'):
    raise SystemExit('Refusing reconcile: DATABASE_URL must target a PostgreSQL test_* database')
from app.database import SessionLocal
from app.knowledge.repository import reconcile
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.vector_store import MilvusIndex
settings = KnowledgeSettings.from_env()
index = MilvusIndex(settings.milvus_uri, settings.generation)
try:
    # Create/load the selected versioned collection if needed. This is additive;
    # the old generation's collection is never dropped or modified.
    index.ensure_collection(settings.generation)
    with SessionLocal() as session, session.begin():
        result = reconcile(session, index, settings.generation, repair=args.repair)
    print({'generation': settings.generation, 'collection': index.collection_name, 'repair': args.repair, **result})
finally:
    index.close()
