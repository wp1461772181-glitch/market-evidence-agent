import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from .database import Base, SessionLocal, engine
from .dashboard import dashboard_evaluation, dashboard_price_history, dashboard_snapshot_entries
from .evidence_revision import (
    EvidenceRevisionError,
    create_evidence_revision,
    create_evidence_revision_tables,
    evidence_revisions_for_symbol,
)
from .models import Forecast, ForecastRevision, ForecastRevisionEvidence, ForecastSnapshot, ResearchRun
from .manual_evidence import (
    MAX_UPLOAD_BYTES,
    ManualEvidenceError,
    content_preview,
    create_uploaded_evidence,
    create_uploaded_evidence_table,
    uploaded_evidence_for_symbol,
)
from .local_model_profiles import router as local_model_profiles_router
from .agent_chat_api import router as agent_chat_router
from .agent_chat_stream import router as agent_chat_stream_router
from .knowledge.api import router as knowledge_router
from .agent_chat_identity import router as agent_chat_identity_router
from .agent_chat_models import AgentConversation, AgentMessage, AgentFAQ
from .agent_workflow.models import AgentConsent, AgentWebSource
from .agent_faq_seed import seed_faq
from .material_analysis_api import router as material_analysis_router
from .localization_api import router as localization_router
from .event_provider import EventProviderError, configured_deepseek_model, create_deepseek_provider_from_env
from .forecast_refresh import ForecastRefreshError, get_forecast_refresh_report, run_forecast_refresh, target_window
from .forecast_v2_api import router as forecast_v2_router
from .on_demand_forecast import OnDemandForecastError, create_on_demand_forecast
from .research_workflow import (
    DEFAULT_DOCUMENT_DIRECTORY,
    ResearchWorkflowError,
    run_research,
)
from .sec_filings import (
    SecFilingNotFoundError,
    SecFilingsError,
    create_sec_filing_inventory_table,
    fetch_inventory_content,
    inventory_for_symbol,
    review_inventory_filing,
    scan_sec_filings,
)
from .market_data import fetch_daily_prices
from .market_data_ingestion import ingest_market_data
from .forecast_contract import latest_completed_xnys_session
from .symbols import normalize_market_symbol
from .schemas import (
    ForecastRequest,
    ForecastRunResponse,
    ForecastRunRequest,
    ForecastRefreshRequest,
    ForecastRefreshResponse,
    ForecastResponse,
    DashboardResponse,
    EvidenceRevisionInventoryResponse,
    EvidenceRevisionRequest,
    EvidenceRevisionResponse,
    ForecastSnapshotResponse,
    ForecastSnapshotTimelineEntry,
    ForecastSnapshotTimelineResponse,
    ResearchRunRequest,
    ResearchRunResponse,
    SecFilingContentResponse,
    SecFilingInventoryItem,
    SecFilingInventoryResponse,
    SecFilingReviewRequest,
    SecFilingScanResponse,
    UploadedEvidenceItem,
    UploadedEvidenceResponse,
)
from .services import MODEL_VERSION, is_valid_symbol, mock_forecast, normalize_symbol
from .agent_chat_middleware import AgentChatBodyLimitMiddleware, create_agent_chat_access_middleware


# FastAPI 应用入口：创建 app、挂载 V2/材料/本地化路由，并保留旧版研究、行情、上传与预测端点。
# V3 主链从 forecast_v2_router 进入；startup 只创建指定旧表，V2 schema 由显式迁移管理。
# app 在此创建；V2、材料分析和本地化路由在这里注册。
# 旧接口与 V3 router 并存；startup 自动建表范围刻意排除了 V2 与可选材料表。
PUBLIC_API_MODE = os.getenv("PUBLIC_API_MODE", "false").strip().lower() in {"1", "true", "yes"}
APP_API_ACCESS_KEY = os.getenv("APP_API_ACCESS_KEY", "")

app = FastAPI(title="Market Evidence Agent", version="0.1.0")

@app.exception_handler(RequestValidationError)
async def safe_agent_chat_validation(request: Request, exc: RequestValidationError):
    if request.url.path in {"/agent-chat/hello", "/agent-chat/messages", "/agent-chat/stream", "/agent-chat/models", "/agent-chat/personal-login",
                            "/agent-chat/background-model"} or request.url.path.startswith(("/agent-chat/model-catalog", "/agent-chat/bots")):
        from .agent_chat_api import safe_validation_response
        return await safe_validation_response(request, exc)
    return await request_validation_exception_handler(request, exc)

allowed_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

if allowed_origins:
    cors_middleware_enabled = True
else:
    cors_middleware_enabled = False
app.include_router(agent_chat_router)
app.include_router(local_model_profiles_router)
app.include_router(agent_chat_stream_router)
from .agent_bots_api import router as agent_bots_router
app.include_router(agent_bots_router)
app.include_router(knowledge_router)
app.include_router(agent_chat_identity_router)
app.include_router(forecast_v2_router)
app.include_router(material_analysis_router)
app.include_router(localization_router)


# 公开模式下对业务请求校验访问头并禁缓存；健康检查和预检请求放行。
# Resolve the active application configuration when a request arrives. This
# keeps middleware policy aligned with lifespan/config overrides in tests and
# embedded deployments; production still reads the same fail-closed settings.
app.middleware("http")(create_agent_chat_access_middleware(lambda: PUBLIC_API_MODE, lambda: APP_API_ACCESS_KEY))


# Register CORS after the authentication middleware so browser clients receive
# CORS headers even on the 401 response that triggers the personal-login prompt.
if cors_middleware_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Idempotency-Key", "Authorization", "X-App-Access-Key", "X-Chat-Session"],
        expose_headers=["X-Model-Profile-Path", "X-Model-Profile-Storage"],
    )
app.add_middleware(AgentChatBodyLimitMiddleware, allowed_origins=allowed_origins)


# 启动时检查公开模式配置并创建允许自动创建的旧表，不自动迁移 V2 表。
@app.on_event("startup")
def create_tables() -> None:
    from .agent_workflow.checkpoints import checkpoint_ready
    from .agent_workflow.settings import checkpoint_database_url
    # A context budget that cannot fit one turn is a configuration error: say so at start-up
    # rather than at the first request that needs history.
    from .agent_context import startup_self_check
    app.state.agent_context = startup_self_check()
    try:
        app.state.agent_checkpoint_ready = checkpoint_ready(checkpoint_database_url())
    except RuntimeError:
        app.state.agent_checkpoint_ready = False
    if PUBLIC_API_MODE:
        from .agent_chat_identity import _load_config
        if _load_config() is None:
            raise RuntimeError("PUBLIC_API_MODE requires a valid personal chat login configuration")
        if not allowed_origins or "*" in allowed_origins:
            raise RuntimeError("PUBLIC_API_MODE requires exact CORS_ALLOWED_ORIGINS; wildcard origins are not allowed")
    # V2 schema changes are applied only by its explicit, locked migration.
    # Keep the longstanding startup behavior for legacy tables alone.
    Base.metadata.create_all(
        bind=engine,
        tables=[table for table in Base.metadata.tables.values()
                if not table.name.endswith("_v2")
                and not table.name.startswith("knowledge_")
                and table.name not in {"material_analysis_jobs", "material_analysis_versions", "material_analysis_job_model_configs", "material_analysis_settings", "ai_content_translations"}],
    )
    create_sec_filing_inventory_table()
    create_uploaded_evidence_table()
    create_evidence_revision_tables()
    from .agent_chat_store import ensure_conversation_archive_column
    ensure_conversation_archive_column()
    # Memory layers (second edition, stage B): a profile item's bot; NULL (every existing item) is global.
    from .agent_profile import ensure_bot_column as ensure_profile_bot_column
    ensure_profile_bot_column()
    # Bots (second edition): the conversation's bot column, the earlier model settings folded into the
    # catalog once, and a line per catalog model saying whether it can be used (never a key).
    from . import agent_model_catalog as model_catalog
    from .agent_recall import safe_hook as _safe
    _safe(model_catalog.migrate_legacy)
    model_catalog.log_startup_report()
    # Conversations stored before the past-conversation index existed (agent_recall) are indexed once.
    from .agent_recall import backfill as backfill_recall, safe_hook
    safe_hook(backfill_recall)
    with SessionLocal() as session:
        seed_faq(session)
        session.commit()
    if os.getenv("RAG_ENABLED", "false").strip().lower() in {"1", "true", "yes"}:
        from .knowledge.runtime import start_runtime_background
        start_runtime_background()
        from .knowledge.source_sync import start_source_sync
        start_source_sync(SessionLocal)

@app.on_event("shutdown")
def stop_knowledge_runtime() -> None:
    from .knowledge.source_sync import stop_source_sync
    stop_source_sync()
    from .knowledge.runtime import stop_runtime
    stop_runtime()


# 为旧版 API 依赖提供一次请求范围的 SQLAlchemy 会话。
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 返回轻量存活状态，不访问数据库。
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/agent-workflow")
def agent_workflow_health() -> dict[str, object]:
    from .agent_workflow.checkpoints import checkpoint_ready
    from .agent_workflow.settings import checkpoint_database_url
    try:
        ready = checkpoint_ready(checkpoint_database_url())
    except RuntimeError:
        ready = False
    app.state.agent_checkpoint_ready = ready
    return {"checkpoint_ready": ready, "tavily_configured": bool(os.getenv("TAVILY_API_KEY")),
            "web_backend": os.getenv("WEB_RESEARCH_BACKEND", "tavily")}


@app.get("/health/knowledge")
def knowledge_health() -> dict[str, object]:
    from .knowledge.runtime import get_runtime
    from .knowledge.settings import KnowledgeSettings
    configured=KnowledgeSettings.from_env(validate_model=False)
    runtime=get_runtime()
    if runtime is None:
        return {"enabled":configured.enabled,"ready":False,"milvus":"unavailable"}
    age=max(0.0,__import__('time').monotonic()-runtime.last_health_check)
    healthy=bool(runtime.ready and age<=30.0)
    if not healthy:runtime.ready=False
    return {"enabled":True,"ready":healthy,"generation":runtime.settings.generation,
            "milvus":"healthy" if healthy else "stale_or_unavailable","health_age_seconds":round(age,1)}


# 调用旧研究工作流并保存研究运行结果，不会自动生成 V3 预测版本。
@app.post("/research-runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_research_run(payload: ResearchRunRequest, db: Session = Depends(get_db)) -> ResearchRun:
    try:
        model = configured_deepseek_model()
        return run_research(
            symbol=payload.symbol,
            as_of_time=payload.as_of_time,
            document_ids=payload.document_ids,
            db=db,
            model=model,
            document_directory=DEFAULT_DOCUMENT_DIRECTORY,
            provider_factory=lambda: create_deepseek_provider_from_env(model=model),
        )
    except (ResearchWorkflowError, EventProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 按 UUID 读取旧研究任务结果。
@app.get("/research-runs/{run_id}", response_model=ResearchRunResponse)
def get_research_run(run_id: UUID, db: Session = Depends(get_db)) -> ResearchRun:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="research run not found")
    return run


# 运行旧 Week 8 滚动刷新流程并返回修订报告。
@app.post("/forecast-refresh-runs", response_model=ForecastRefreshResponse, status_code=status.HTTP_201_CREATED)
def create_forecast_refresh_run(
    payload: ForecastRefreshRequest, db: Session = Depends(get_db)
) -> dict:
    """Run one saved-event rolling refresh using only fixed trusted local inputs."""
    try:
        model = configured_deepseek_model()
        result = run_forecast_refresh(
            symbol=payload.symbol,
            before_as_of_time=payload.before_as_of_time,
            after_as_of_time=payload.after_as_of_time,
            document_id=payload.document_id,
            db=db,
            extraction_provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            research_provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            model=model,
        )
        return result.as_dict()
    except (ForecastRefreshError, EventProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 读取旧刷新运行报告。
@app.get("/forecast-refresh-runs/{snapshot_id}", response_model=ForecastRefreshResponse)
def get_forecast_refresh_run(snapshot_id: UUID, db: Session = Depends(get_db)) -> dict:
    try:
        return get_forecast_refresh_report(snapshot_id=snapshot_id, db=db).as_dict()
    except ForecastRefreshError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 组合旧版离线快照、评估及行情数据供工作台展示。
@app.get("/dashboard/{symbol}", response_model=DashboardResponse)
def get_dashboard(symbol: str, db: Session = Depends(get_db)) -> dict:
    """Read saved forecast evidence for the simple Week 9 dashboard.

    This endpoint deliberately never creates a mock forecast, loads a model,
    calls an LLM, or modifies a database row.
    """
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise HTTPException(status_code=422, detail="symbol must contain 1-5 ASCII letters")

    snapshots = (
        db.query(ForecastSnapshot)
        .filter(ForecastSnapshot.symbol == normalized_symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    evidence_rows = (
        db.query(ForecastRevisionEvidence)
        .join(ForecastSnapshot, ForecastRevisionEvidence.snapshot_id == ForecastSnapshot.id)
        .filter(ForecastSnapshot.symbol == normalized_symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    refresh_reports: list[dict] = []
    for evidence in evidence_rows:
        try:
            refresh_reports.append(get_forecast_refresh_report(snapshot_id=evidence.snapshot_id, db=db).as_dict())
        except ForecastRefreshError as exc:
            raise HTTPException(status_code=500, detail="saved forecast refresh report is incomplete") from exc

    return {
        "symbol": normalized_symbol,
        "snapshots": dashboard_snapshot_entries(snapshots, db),
        "refresh_reports": refresh_reports,
        "evaluation": dashboard_evaluation(),
        "price_history": dashboard_price_history(normalized_symbol, db),
    }


# 扫描 SEC 申报并更新旧库存，不等同于冻结进预测的证据版本。
@app.post(
    "/filing-inventories/{symbol}/scan",
    response_model=SecFilingScanResponse,
    status_code=status.HTTP_201_CREATED,
)
def scan_filing_inventory(symbol: str, db: Session = Depends(get_db)) -> dict:
    """Discover recent official SEC metadata for one supported symbol.

    The scan does not download filing bodies, call a model, or create a
    prediction.  A filing body is fetched only by the explicit per-filing
    endpoint below.
    """
    observed_at = datetime.now(UTC)
    try:
        filings, created_count, skipped_count = scan_sec_filings(
            symbol=symbol, db=db, observed_at=observed_at
        )
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_symbol = normalize_symbol(symbol)
    return {
        "symbol": normalized_symbol,
        "cik": filings[0].cik if filings else None,
        "discovered_count": len(filings),
        "created_count": created_count,
        "skipped_count": skipped_count,
        "observed_at": observed_at,
        "filings": filings,
    }


# 读取指定股票的 SEC 申报库存。
@app.get("/filing-inventories/{symbol}", response_model=SecFilingInventoryResponse)
def get_filing_inventory(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        filings = inventory_for_symbol(symbol=symbol, db=db)
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_symbol(symbol), "filings": filings}


# 抓取/读取申报正文并返回覆盖状态。
@app.post("/market-data/{symbol}/initialize")
def initialize_market_data(symbol: str) -> dict:
    """Explicitly seed at most one year of daily prices for one valid ticker."""
    try:
        normalized = normalize_market_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    end = latest_completed_xnys_session(datetime.now(UTC))
    start = end - timedelta(days=370)
    try:
        summary = ingest_market_data([normalized], start, end, datetime.now(UTC), fetcher=fetch_daily_prices)
    except Exception as exc:
        # Provider/database details can contain private response data; expose a
        # stable upstream failure while preserving the original server log.
        raise HTTPException(status_code=502, detail="Price initialization failed; check provider settings and retry") from exc
    return {
        "symbol": normalized,
        "from": start.isoformat(),
        "through": end.isoformat(),
        "inserted_count": summary.inserted_count,
        "skipped_count": summary.skipped_count,
    }


@app.post(
    "/filing-inventories/{symbol}/{accession_number}/fetch",
    response_model=SecFilingContentResponse,
)
def fetch_filing_content(
    symbol: str,
    accession_number: str,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Fetch or refresh one inventoried SEC filing and keep its complete text."""
    try:
        filing, cache_hit = fetch_inventory_content(
            symbol=symbol,
            accession_number=accession_number,
            db=db,
            observed_at=datetime.now(UTC),
            force_refresh=refresh,
        )
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = SecFilingInventoryItem.model_validate(filing).model_dump()
    return {
        **item,
        "content_excerpt": filing.content_excerpt,
        "content_length": filing.content_length,
        "cache_hit": cache_hit,
    }


# 记录人工来源相关性审核；审核不验证申报事实或预测方向。
@app.post(
    "/filing-inventories/{symbol}/{accession_number}/review",
    response_model=SecFilingInventoryItem,
)
def review_filing_inventory(
    symbol: str,
    accession_number: str,
    payload: SecFilingReviewRequest,
    db: Session = Depends(get_db),
) -> object:
    """Record a human decision about source relevance only."""
    try:
        return review_inventory_filing(
            symbol=symbol,
            accession_number=accession_number,
            decision=payload.decision,
            note=payload.note,
            db=db,
            reviewed_at=datetime.now(UTC),
        )
    except SecFilingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 接收并保存用户上传材料及元数据，返回上传证据响应。
@app.post(
    "/uploaded-evidence/{symbol}",
    response_model=UploadedEvidenceItem,
    status_code=status.HTTP_201_CREATED,
)
async def upload_manual_evidence(
    symbol: str,
    response: Response,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str, Form(...)],
    source_url: Annotated[str, Form(...)],
    published_at: Annotated[datetime, Form(...)],
    credibility_stars: Annotated[int, Form(...)],
    credibility_reason: Annotated[str, Form(...)],
    impact_severity: Annotated[str, Form(...)],
    x_chat_session: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Store user-selected media material as unconfirmed review evidence only."""
    raw_content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        evidence, created = create_uploaded_evidence(
            symbol=symbol,
            title=title,
            source_url=source_url,
            published_at=published_at,
            credibility_stars=credibility_stars,
            credibility_reason=credibility_reason,
            impact_severity=impact_severity,
            filename=file.filename,
            content=raw_content,
            db=db,
        )
    except ManualEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
    if not created:
        response.status_code = status.HTTP_200_OK
    elif x_chat_session and 32 <= len(x_chat_session) <= 256 and not any(ord(c) < 32 for c in x_chat_session):
        # Bind only a newly created upload to the requesting opaque chat session. A duplicate UUID never proves ownership.
        from sqlalchemy import inspect
        if inspect(db.get_bind()).has_table("knowledge_upload_owners"):
            from .agent_chat_store import owner_hash
            from .knowledge.models import KnowledgeUploadOwner
            db.add(KnowledgeUploadOwner(upload_id=evidence.id, owner_hash=owner_hash(x_chat_session)))
            db.commit()
    return _uploaded_evidence_item(evidence)


# 列出指定股票的人工上传来源。
@app.get("/uploaded-evidence/{symbol}", response_model=UploadedEvidenceResponse)
def get_uploaded_evidence(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        items = uploaded_evidence_for_symbol(symbol=symbol, db=db)
    except ManualEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_symbol(symbol), "items": [_uploaded_evidence_item(item) for item in items]}


def _uploaded_evidence_item(evidence: object) -> dict:
    item = UploadedEvidenceItem.model_validate(evidence).model_dump()
    item["content_preview"] = content_preview(getattr(evidence, "content_text"))
    return item


# 按显式选中的旧版证据路径生成修订记录。
@app.post(
    "/evidence-revisions/{symbol}",
    response_model=EvidenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_selected_evidence_revision(
    symbol: str, payload: EvidenceRevisionRequest, db: Session = Depends(get_db)
) -> dict:
    """Bind one later source to the exact saved forecast version selected by the user."""
    try:
        normalized_symbol = normalize_symbol(symbol)
        if not is_valid_symbol(normalized_symbol):
            raise EvidenceRevisionError("symbol must contain 1-5 ASCII letters")
        parent = db.get(ForecastSnapshot, payload.parent_snapshot_id)
        if parent is None:
            raise EvidenceRevisionError("selected forecast snapshot was not found")
        if parent.symbol != normalized_symbol:
            raise EvidenceRevisionError("selected forecast symbol does not match the request path")
        model = configured_deepseek_model()
        result = create_evidence_revision(
            parent_snapshot_id=payload.parent_snapshot_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            mode=payload.mode,
            db=db,
            provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            model=model,
        )
        if result.revision.symbol != normalized_symbol:
            raise EvidenceRevisionError("selected forecast symbol does not match the request path")
        return result.as_dict()
    except (EvidenceRevisionError, EventProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 读取旧版证据修订历史。
@app.get("/evidence-revisions/{symbol}", response_model=EvidenceRevisionInventoryResponse)
def get_evidence_revisions(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        normalized_symbol = normalize_symbol(symbol)
        return {
            "symbol": normalized_symbol,
            "revisions": [result.as_dict() for result in evidence_revisions_for_symbol(symbol=normalized_symbol, db=db)],
        }
    except EvidenceRevisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 保留的 Week 1 确定性 mock-v1 接口，写入旧 forecasts 表。
@app.post("/forecasts", response_model=ForecastResponse, status_code=status.HTTP_201_CREATED)
def create_forecast(payload: ForecastRequest, db: Session = Depends(get_db)) -> Forecast:
    symbol = normalize_symbol(payload.symbol)
    if not is_valid_symbol(symbol):
        raise HTTPException(status_code=422, detail="symbol must contain 1-5 ASCII letters")
    bullish, neutral, bearish = mock_forecast(symbol)
    forecast = Forecast(
        symbol=symbol,
        bullish_probability=bullish,
        neutral_probability=neutral,
        bearish_probability=bearish,
        model_version=MODEL_VERSION,
    )
    db.add(forecast)
    db.commit()
    db.refresh(forecast)
    return forecast


# 调用离线数值模型并保存带输入指纹的快照；这是历史实验路径，不是 V3 Agent。
@app.post("/forecast-runs", response_model=ForecastRunResponse, status_code=status.HTTP_201_CREATED)
def create_forecast_run(payload: ForecastRunRequest, db: Session = Depends(get_db)) -> dict:
    """Refresh observed daily bars and append one real numeric forecast snapshot."""
    try:
        snapshot = create_on_demand_forecast(symbol=payload.symbol, db=db)
        return {
            **ForecastSnapshotResponse.model_validate(snapshot).model_dump(),
            "cutoff_date": snapshot.feature_trading_date,
            "target_window": target_window(snapshot.feature_trading_date).as_dict(),
            "model_status": "experimental_offline_model",
            "limitations": [
                "This is an experimental offline market-feature model, not investment advice or a price target.",
                "The model uses observed daily bars only; SEC filings and other evidence are not automatically used in this numeric forecast.",
            ],
        }
    except OnDemandForecastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# 读取一条离线数值预测快照。
@app.get("/forecast-snapshots/{snapshot_id}", response_model=ForecastSnapshotResponse)
def get_forecast_snapshot(snapshot_id: UUID, db: Session = Depends(get_db)) -> ForecastSnapshot:
    snapshot = db.get(ForecastSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="forecast snapshot not found")
    return snapshot


# 读取旧快照及其修订链。
@app.get("/forecast-snapshots/{snapshot_id}/timeline", response_model=ForecastSnapshotTimelineResponse)
def get_forecast_snapshot_timeline(
    snapshot_id: UUID, db: Session = Depends(get_db)
) -> ForecastSnapshotTimelineResponse:
    snapshot = db.get(ForecastSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="forecast snapshot not found")

    entries, root_snapshot_id = _forecast_timeline(snapshot, db)
    return ForecastSnapshotTimelineResponse(root_snapshot_id=root_snapshot_id, snapshots=entries)


# 将旧版快照与父子修订行组装为带版本序号的响应条目。
def _forecast_timeline(
    snapshot: ForecastSnapshot, db: Session
) -> tuple[list[ForecastSnapshotTimelineEntry], UUID]:
    """Return every saved branch sharing this snapshot's root.

    Legacy ``ForecastRevision`` links are linear, while evidence-led review can
    create several alternatives from the same parent.  Reuse the dashboard's
    validated parent-link traversal so a side-table evidence child never
    appears as a separate root through this endpoint.
    """
    symbol_snapshots = (
        db.query(ForecastSnapshot)
        .filter(ForecastSnapshot.symbol == snapshot.symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    entries = dashboard_snapshot_entries(symbol_snapshots, db)
    selected = next((entry for entry in entries if entry.id == snapshot.id), None)
    if selected is None:  # pragma: no cover - the selected row came from this query.
        raise HTTPException(status_code=500, detail="forecast timeline is missing its selected snapshot")
    root_snapshot_id = selected.root_snapshot_id
    return [entry for entry in entries if entry.root_snapshot_id == root_snapshot_id], root_snapshot_id
