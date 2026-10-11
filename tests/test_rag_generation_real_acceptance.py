"""Opt-in independent acceptance with the real provider and saved AMD evidence."""
import hashlib
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv("KNOWLEDGE_GENERATION_REAL") != "1", reason="explicit real-provider acceptance")


def test_real_provider_sse_answers_and_refusals(disposable_database, monkeypatch, tmp_path):
    from dotenv import load_dotenv
    from fastapi.testclient import TestClient
    from test_knowledge_amd_real import test_fixed_amd_acceptance_real_model_and_upsert_resume
    from app.database import Base, engine
    from app.main import app
    from app.knowledge.embedding import DenseEmbedder
    from app.knowledge.runtime import KnowledgeRuntime
    from app.knowledge.settings import KnowledgeSettings
    from app.knowledge.vector_store import MilvusIndex
    import app.knowledge.runtime as runtime_module

    # The existing gate creates only a disposable PostgreSQL corpus and unique
    # Milvus generation; no developer records or personal conversations are used.
    assert engine.url.database.startswith("test_")
    report_path = tmp_path / "corpus.json"
    monkeypatch.setenv("KNOWLEDGE_AMD_REPORT", str(report_path))
    test_fixed_amd_acceptance_real_model_and_upsert_resume(disposable_database, monkeypatch, tmp_path)
    generation = json.loads(report_path.read_text())["generation"]
    Base.metadata.create_all(engine)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    assert api_key, "configured local DeepSeek key is required; never print it"
    settings = KnowledgeSettings.from_env()
    from dataclasses import replace
    settings = replace(settings, generation=generation, strategy="hybrid", enabled=True)
    runtime = KnowledgeRuntime(settings, DenseEmbedder(settings.model_path, cpu_threads=2), MilvusIndex(settings.milvus_uri, generation), 0.0)
    runtime.warm()
    previous = runtime_module._runtime
    runtime_module._runtime = runtime
    monkeypatch.setenv("RAG_ENABLED", "true")
    credential = hashlib.sha256(b"independent disposable SSE acceptance").hexdigest()
    cases = [
        ("supported", "请通过知识库语义检索 AMD 的已保存原文，回答收购 World Labs 的金额和支付方式，注明引用。", False),
        ("unknown_revenue", "请通过知识库语义检索 AMD 的已保存原文，World Labs 2025 年营收具体是多少？资料没披露就明确说证据不足。", True),
        ("unknown_eps", "请通过知识库语义检索 AMD 的已保存原文，这笔收购给 AMD EPS 增加了多少钱？资料没披露就明确说证据不足。", True),
    ]
    outcomes = []
    try:
        client = TestClient(app)  # No lifespan: no production/background sync.
        for name, question, refusal in cases:
            response = client.post("/agent-chat/stream", headers={"X-Chat-Session": credential}, json={
                "provider": "deepseek", "model": "deepseek-chat", "api_key": api_key, "message": question})
            events = []
            for block in response.text.split("\n\n"):
                lines = block.splitlines()
                kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
                data = next((line[6:] for line in lines if line.startswith("data: ")), None)
                if kind and data:
                    events.append((kind, json.loads(data)))
            answer = "".join(value.get("text", "") for kind, value in events if kind == "delta")
            tools = [value["name"] for kind, value in events if kind == "tool_start"]
            citations = [value for kind, value in events if kind == "citations"]
            errors = [value for kind, value in events if kind == "error"]
            passed = response.status_code == 200 and not errors and bool(answer) and "query_materials" in tools
            if refusal:
                passed &= any(term in answer for term in ("不足", "没有披露", "未披露", "无法核实", "没有找到"))
            else:
                passed &= ("82亿" in answer.replace(" ", "") or "8.2" in answer) and any(term in answer for term in ("股票", "普通股", "common stock")) and bool(citations)
            outcomes.append({"id": name, "passed": bool(passed), "tools": tools, "answer": answer,
                             "citations": citations, "errors": errors})
        target = Path(os.getenv("KNOWLEDGE_GENERATION_REPORT", str(tmp_path / "generation-report.json")))
        target.write_text(json.dumps({"scope": "real DeepSeek SSE, disposable saved-source corpus", "cases": outcomes}, ensure_ascii=False, indent=2))
        assert all(row["passed"] for row in outcomes), [(row["id"], row["tools"], row["errors"]) for row in outcomes]
    finally:
        runtime_module._runtime = previous
        runtime.close()
