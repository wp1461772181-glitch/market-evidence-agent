from __future__ import annotations
from pathlib import Path
import numpy as np
class DenseEmbedder:
    def __init__(self, model_path: str|Path, *, cpu_threads: int=4):
        import torch
        from FlagEmbedding import BGEM3FlagModel
        from transformers import AutoTokenizer
        torch.set_num_threads(cpu_threads)
        self.tokenizer=AutoTokenizer.from_pretrained(str(model_path),local_files_only=True)
        self.model_path=str(model_path);self.model_class=BGEM3FlagModel;self.model=None
        self.reload_model()
    def reload_model(self):
        if self.model is None:self.model=self.model_class(self.model_path,use_fp16=False,devices=['cpu'])
    def release_model(self):
        if self.model is not None:
            del self.model;self.model=None
            import gc;gc.collect()
    def encode(self, texts: list[str]) -> list[list[float]]:
        self.reload_model()
        result=self.model.encode(texts,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs']
        values=np.asarray(result,dtype=np.float32)
        if values.ndim!=2 or values.shape[1]!=1024 or not np.isfinite(values).all():
            raise ValueError('BGE-M3 returned invalid dense vectors')
        return values.tolist()
    def count_tokens(self,text: str) -> int:
        return len(self.tokenizer.encode(text,add_special_tokens=True))
