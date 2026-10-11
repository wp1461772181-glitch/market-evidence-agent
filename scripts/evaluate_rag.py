"""Run the fixed real BGE/Milvus/PG evaluation in an automatically dropped test_* database."""
from __future__ import annotations
import os,subprocess,sys

def main():
    env=os.environ.copy();env['KNOWLEDGE_REAL_INTEGRATION']='1'
    env.setdefault('TOKENIZERS_PARALLELISM','false');env.setdefault('OMP_NUM_THREADS','4');env.setdefault('MKL_NUM_THREADS','4')
    raise SystemExit(subprocess.call([sys.executable,'-m','pytest','-q','tests/test_knowledge_evaluation_real.py'],env=env))
if __name__=='__main__':main()
