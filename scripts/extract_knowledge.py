from __future__ import annotations
import argparse,time,hashlib
from datetime import UTC,datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.knowledge.chunking import CHUNKER_VERSION,FORMAT_VERSION
from app.knowledge.extraction import create_operator_provider,extract_once,opted_in_turns,review_candidate
from app.knowledge.models import KnowledgeCandidate,KnowledgeExtractionRun
from app.knowledge.repository import embedding_generation
from app.knowledge.runtime import get_tokenizer
from app.knowledge.settings import MODEL_ID,MODEL_REVISION

def _generation():return embedding_generation(MODEL_ID,MODEL_REVISION,FORMAT_VERSION,CHUNKER_VERSION)
def run_once(ids):
    provider=create_operator_provider()
    scope_key=hashlib.sha256('\n'.join(sorted(set(ids))).encode()).hexdigest()
    with SessionLocal() as session:
        cursor=session.scalar(select(KnowledgeExtractionRun.cursor).where(KnowledgeExtractionRun.scope_key==scope_key,
          KnowledgeExtractionRun.batch_status=='complete').order_by(KnowledgeExtractionRun.created_at.desc()).limit(1))
    counts=extract_once(provider,cursor,opted_in_turns(ids),provider,max_turns=50,scope_key=scope_key)
    print({'processed':counts['processed'],'candidates':counts['candidates'],'skipped':counts['skipped'],'cursor':counts['cursor']})
def main():
    parser=argparse.ArgumentParser(description='Extract source-linked review candidates from explicitly opted-in completed turns.')
    sub=parser.add_subparsers(dest='command',required=True)
    once=sub.add_parser('once');once.add_argument('--conversation',action='append',required=True)
    daemon=sub.add_parser('interval');daemon.add_argument('--conversation',action='append',required=True);daemon.add_argument('--seconds',type=int,required=True)
    review=sub.add_parser('review');review.add_argument('candidate_id');review.add_argument('--decision',choices=['approve','reject'],required=True)
    review.add_argument('--reviewer',required=True);review.add_argument('--scope',choices=['public','private'],default='private')
    args=parser.parse_args()
    if args.command=='review':
        with SessionLocal.begin() as session:
            row=session.get(KnowledgeCandidate,args.candidate_id)
            status=review_candidate(session,args.candidate_id,args.decision,args.reviewer,args.scope,get_tokenizer(),_generation()) if row else 'candidate_not_found'
        print({'status':status});return
    if args.command=='once':run_once(args.conversation);return
    if args.seconds<60:parser.error('interval must be at least 60 seconds')
    while True:
        run_once(args.conversation)
        time.sleep(args.seconds)
if __name__=='__main__':main()
