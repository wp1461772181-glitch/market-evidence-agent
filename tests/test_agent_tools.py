from app.agent_tools import TOOL_REGISTRY, tool_schemas, normalize_agent_symbol
import asyncio


def test_fixed_read_only_registry_and_provider_schemas():
    assert set(TOOL_REGISTRY) == {"query_stock_prices", "query_latest_forecast", "query_materials", "query_sec_filings", "query_research_faq"}
    assert {x["function"]["name"] for x in tool_schemas()} == set(TOOL_REGISTRY)
    assert normalize_agent_symbol(" brk-b ") == "BRK.B"
    assert normalize_agent_symbol("amd") == "AMD"


def test_tool_schemas_are_strict_and_limited():
    price = TOOL_REGISTRY["query_stock_prices"].args_schema
    assert price.model_validate({"symbol": "amd"}).symbol == "AMD"
    try:
        price.model_validate({"symbol": "AMD", "sql": "DROP TABLE"})
    except Exception:
        pass
    else:
        raise AssertionError("unknown fields must be rejected")
    assert price.model_validate({"symbol": "BRK-B"}).symbol == "BRK.B"
    assert price.model_json_schema()["properties"]["limit"]["maximum"] == 30


def test_sec_without_form_omits_nullable_parameter(monkeypatch):
    import app.agent_tools as tools
    seen=[]
    async def query(sql,params): seen.append((sql,params)); return []
    monkeypatch.setattr(tools,"_query",query)
    asyncio.run(tools.query_sec_filings.ainvoke({"symbol":"AAPL","form":None}))
    assert ":form" not in seen[0][0]
    assert "form" not in seen[0][1]


def test_material_search_delegates_natural_language_to_dense_retrieval(monkeypatch):
    import app.agent_tools as tools
    monkeypatch.setenv('RAG_ENABLED','true')
    seen=[]
    async def retrieve(symbol,query,limit):
        seen.append((symbol,query,limit))
        return {"ok":True,"data":[],"source":"knowledge_documents","as_of":None}
    monkeypatch.setattr(tools,"retrieve_materials",retrieve)
    result=asyncio.run(tools.query_materials.ainvoke({"symbol":"AAPL","keyword":"查FY2025收入"}))
    assert seen==[("AAPL","查FY2025收入",5)]
    assert result=={"ok":True,"data":[],"source":"knowledge_documents","as_of":None}
    schema=tools.query_materials.args_schema.model_json_schema()
    assert set(schema["properties"])=={"symbol","keyword","limit"}


def test_forecast_jsonb_null_falls_through_to_baseline(monkeypatch):
    import app.agent_tools as tools
    async def query(sql,params):
        assert "forecast_versions_v2" in sql and "forecast_snapshots" not in sql
        return [{"id":"v","version_no":1,"decision_probabilities":"null","joint_probabilities":None,"baseline_probabilities":{"bearish":.2,"neutral":.3,"bullish":.5},"research_brief":None,"created_at":None}]
    monkeypatch.setattr(tools,"_query",query)
    result=asyncio.run(tools.query_latest_forecast.ainvoke({"symbol":"AAPL"}))
    assert result["data"][0]["probabilities"]=={"bearish":.2,"neutral":.3,"bullish":.5}


def test_legacy_materials_recovers_when_owner_table_is_absent(disposable_database):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4
    from app.database import Base, engine
    from app.database import SessionLocal
    from app.models import SecFilingInventory, UploadedEvidence
    from app.knowledge.models import KnowledgeUploadOwner
    from app.knowledge.runtime import research_context
    import app.agent_tools as tools

    required = [UploadedEvidence.__table__, SecFilingInventory.__table__, KnowledgeUploadOwner.__table__]
    Base.metadata.create_all(engine, tables=required)
    upload_id = uuid4()
    now = datetime.now(UTC)
    body = "legacy uploaded evidence"
    excerpt = "public SEC excerpt"
    with SessionLocal() as session:
        session.add(UploadedEvidence(
            id=upload_id, symbol="AAPL", title="Legacy upload", source_url="https://example.test/upload",
            published_at=now, observed_at=now, credibility_stars=3, credibility_reason="fixture",
            impact_severity="medium", filename="fixture.txt", content_sha256="a" * 64,
            raw_content=body.encode(), content_text=body, status="unconfirmed",
        ))
        session.add(SecFilingInventory(
            id=uuid4(), symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1000000:06d}",
            form="10-Q", filed_at=now.date(), primary_document="public excerpt", source_url="https://example.test/sec",
            source="synthetic", review_status="accepted", reviewed_at=now-timedelta(minutes=1), observed_at=now-timedelta(minutes=2),
            content_status="fetched", content_observed_at=now-timedelta(minutes=1), content_excerpt=excerpt,
            content_excerpt_sha256=__import__("hashlib").sha256(excerpt.encode()).hexdigest(),
        ))
        missing_observation = "excerpt without content observation"
        session.add(SecFilingInventory(
            id=uuid4(), symbol="AAPL", cik="0000320193", accession_number=f"0000320193-26-{uuid4().int % 1000000:06d}",
            form="10-Q", filed_at=now.date(), primary_document="missing observation", source_url="https://example.test/sec-2",
            source="synthetic", review_status="accepted", reviewed_at=now-timedelta(minutes=3), observed_at=now-timedelta(minutes=4),
            content_status="fetched", content_observed_at=None, content_excerpt=missing_observation,
            content_excerpt_sha256=__import__("hashlib").sha256(missing_observation.encode()).hexdigest(),
        ))
        session.commit()

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE knowledge_upload_owners RENAME TO knowledge_upload_owners_temporarily_absent")
    try:
        with research_context("verified-owner") as context:
            context.legacy_unmapped_owner = True
            owner_result = asyncio.run(tools._legacy_materials("AAPL", "legacy", 5))
        guest_result = asyncio.run(tools._legacy_materials("AAPL", "legacy", 5))
        assert [row["material_type"] for row in owner_result["data"]] == ["uploaded_media"]
        assert guest_result["data"] == []
        public_result = asyncio.run(tools._legacy_materials("AAPL", "excerpt", 5))
        assert [row["material_type"] for row in public_result["data"]] == ["official_filing"]
        assert datetime.fromisoformat(public_result["as_of"]) == now-timedelta(minutes=1)
        with research_context("verified-owner", cutoff=now-timedelta(seconds=90)):
            too_early = asyncio.run(tools._legacy_materials("AAPL", "excerpt", 5))
        assert too_early["data"] == []
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE knowledge_upload_owners_temporarily_absent RENAME TO knowledge_upload_owners")
    with SessionLocal() as session:
        session.add(KnowledgeUploadOwner(upload_id=upload_id, owner_hash="verified-owner"))
        session.commit()
    with research_context("verified-owner"):
        mapped_owner = asyncio.run(tools._legacy_materials("AAPL", "legacy", 5))
    with research_context("different-owner"):
        mapped_guest = asyncio.run(tools._legacy_materials("AAPL", "legacy", 5))
    assert [row["material_type"] for row in mapped_owner["data"]] == ["uploaded_media"]
    assert mapped_guest["data"] == []
