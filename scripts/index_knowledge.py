from __future__ import annotations
import argparse
from app.database import SessionLocal
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.vector_store import MilvusIndex

def main():
    parser=argparse.ArgumentParser(description='Resume idempotent pending dense knowledge indexing.')
    parser.add_argument('--resume',action='store_true',help='claim pending or expired tasks until the queue is empty')
    parser.add_argument('--max-tasks',type=int,default=10000)
    args=parser.parse_args()
    settings=KnowledgeSettings.from_env(validate_model=True)
    embedder=DenseEmbedder(settings.model_path,cpu_threads=settings.cpu_threads)
    index=MilvusIndex(settings.milvus_uri,settings.generation)
    try: print(index_pending(SessionLocal,embedder,index,settings.generation,max_tasks=args.max_tasks))
    finally:index.close()
if __name__=='__main__':main()
