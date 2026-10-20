"""Which saved materials a manual revision of a forecast may use.

One rule serves the forecast workspace, the agent and the revision API, so a material the user is
offered is one the API accepts and the agent can never pick a source the rule does not allow:

* new: published within seven days after the forecast being revised;
* backfilled: published within the thirty days before it, but only stored after it was made;
* preexisting: published within the thirty days before it and already stored at decision time, but
  not used by that forecast;
* updated / reanalyzed: already used by that forecast, and its content or review state changed since,
  or it was analysed again after the forecast.

Material the forecast already used unchanged, and a copy of used content under another record, is
never new information.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

REVISION_LOOKBACK = timedelta(days=30)
REVISION_LOOKAHEAD = timedelta(days=7)

CATEGORY_LABELS = {
    'new': '预测后发布的新材料',
    'backfilled': '预测前发布、预测后才收录的材料',
    'preexisting': '预测前已入库、本版未采用',
    'updated': '已用材料有更新',
    'reanalyzed': '已用材料有新的分析',
}


# Why a saved material of the symbol cannot revise the forecast, in the user's words.
EXCLUSION_REASONS = {
    'published_after_window': '发布时间晚于预测后 7 天',
    'published_before_window': '发布时间早于预测前 30 天',
    'used_unchanged': '已经用在这次预测里，内容和核验状态都没有变化',
    'same_content_as_used': '与这次预测已用的材料内容相同',
    'not_published_yet': '发布时间晚于现在',
    'other_symbol': '不是这只股票的材料',
    'not_fetched': '原文还没有保存到资料库',
    'not_found': '资料库里没有找到这份材料',
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def revision_window(decision_at: datetime) -> tuple[datetime, datetime]:
    """The publication span a revision may draw on: thirty days before the forecast to seven after."""
    decided = _utc(decision_at)
    return decided - REVISION_LOOKBACK, decided + REVISION_LOOKAHEAD


def window_category(decision_at: datetime, published_at: datetime, observed_at: datetime) -> str | None:
    """'new', 'backfilled' or 'preexisting' for a material the forecast did not use, or None outside
    the publication window."""
    decided, published, observed = _utc(decision_at), _utc(published_at), _utc(observed_at)
    if decided <= published <= decided + REVISION_LOOKAHEAD:
        return 'new'
    if decided - REVISION_LOOKBACK <= published < decided:
        # Stored only after the forecast: information that appeared later. Stored already at decision
        # time but not used by this version: information the forecast left unconsidered, which a
        # revision may still draw on.
        return 'backfilled' if observed > decided else 'preexisting'
    return None


def _filing_published(row) -> datetime | None:
    from .evidence_context import EvidenceContextError, _exact_sec_acceptance
    try:
        return _exact_sec_acceptance(row)
    except EvidenceContextError:
        return None


def _filing_title(row) -> str:
    name = row.content_document_name or row.primary_document or ''
    return f'{row.symbol} {row.form} 申报' + (f'（{name}）' if name else '')


def _used_sources(parent) -> tuple[dict[tuple[str, str], dict], set[str]]:
    used: dict[tuple[str, str], dict] = {}
    hashes: set[str] = set()
    for item in parent.evidence_version_manifest if isinstance(parent.evidence_version_manifest, list) else []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('source_type'), str) and isinstance(item.get('source_id'), str):
            used[(item['source_type'], item['source_id'])] = item
        for key in ('content_sha256', 'content_hash'):
            if isinstance(item.get(key), str):
                hashes.add(item[key])
    return used, hashes


def _changed_since(db: Session, item: dict, row, source_type: str) -> bool:
    """Whether the stored state of a used source differs from the snapshot the forecast froze."""
    from .evidence_context import EvidenceContextError, source_state_differs
    from .forecast_v2_models import EvidenceEventVersionV2
    try:
        frozen = db.get(EvidenceEventVersionV2, UUID(str(item.get('event_version_id') or item.get('id'))))
    except (TypeError, ValueError):
        return False
    if frozen is None:
        return False
    try:
        return source_state_differs(frozen, row, source_type=source_type)
    except EvidenceContextError:
        return False


def _analysed_since(db: Session, item: dict, source_type: str, source_id: str, decided: datetime) -> bool:
    """A successful analysis of the exact content the forecast used, made after the forecast."""
    from .material_analysis_models import MaterialAnalysisVersion
    content_hash = item.get('content_sha256') or item.get('content_hash')
    rows = db.scalars(
        select(MaterialAnalysisVersion).where(
            MaterialAnalysisVersion.source_type == source_type,
            MaterialAnalysisVersion.source_id == UUID(source_id),
            MaterialAnalysisVersion.created_at > decided,
        )
    ).all()
    return any(isinstance(row.source_manifest, dict) and row.source_manifest.get('content_sha256') == content_hash
               for row in rows)


def assess_revision_sources(db: Session, parent) -> list[dict]:
    """Every saved material of the parent's symbol, newest first, each with the category that lets it
    revise ``parent`` or the reason it cannot."""
    from .models import SecFilingInventory, UploadedEvidence
    decided = _utc(parent.decision_at)
    used, used_hashes = _used_sources(parent)
    sources = []
    for row in db.scalars(select(SecFilingInventory).where(
            SecFilingInventory.symbol == parent.symbol, SecFilingInventory.content_status == 'fetched')):
        published = _filing_published(row)
        observed = _utc(row.content_observed_at or row.observed_at)
        if published and observed and row.content_excerpt_sha256:
            sources.append(('official_filing', row, published, observed, row.content_excerpt_sha256,
                            _filing_title(row), f'SEC {row.form}'))
    for row in db.scalars(select(UploadedEvidence).where(UploadedEvidence.symbol == parent.symbol)):
        published, observed = _utc(row.published_at), _utc(row.observed_at)
        if published and observed and row.content_sha256:
            sources.append(('uploaded_media', row, published, observed, row.content_sha256, row.title, '上传材料'))

    now = datetime.now(UTC)
    assessed = []
    for source_type, row, published, observed, content_hash, title, kind in sources:
        source_id = str(row.id)
        item = used.get((source_type, source_id))
        category = reason = None
        if published > now:
            reason = 'not_published_yet'
        elif item is None:
            if content_hash in used_hashes:
                reason = 'same_content_as_used'
            else:
                category = window_category(decided, published, observed)
                if category is None:
                    reason = ('published_after_window' if published > decided + REVISION_LOOKAHEAD
                              else 'published_before_window')
        elif _changed_since(db, item, row, source_type):
            category = 'updated'
        elif _analysed_since(db, item, source_type, source_id, decided):
            category = 'reanalyzed'
        else:
            reason = 'used_unchanged'
        assessed.append({
            'source_type': source_type, 'source_id': source_id, 'title': title, 'kind': kind,
            'published_at': published.isoformat(), 'observed_at': observed.isoformat(),
            'category': category, 'badge': CATEGORY_LABELS.get(category), 'reason': reason,
            'reason_text': EXCLUSION_REASONS.get(reason),
        })
    assessed.sort(key=lambda row: row['published_at'], reverse=True)
    return assessed


def revision_candidates(db: Session, parent) -> list[dict]:
    """The saved materials of the parent's symbol that a revision of ``parent`` may use, newest first."""
    return [{k: v for k, v in row.items() if k not in ('reason', 'reason_text')}
            for row in assess_revision_sources(db, parent) if row['category']]


def unknown_source(db: Session, symbol: str, ref: dict) -> dict:
    """Why a named material is not among the symbol's saved materials at all."""
    from .models import SecFilingInventory, UploadedEvidence
    model = SecFilingInventory if ref.get('source_type') == 'official_filing' else UploadedEvidence
    try:
        row = db.get(model, UUID(str(ref.get('source_id'))))
    except (TypeError, ValueError):
        row = None
    reason = 'not_found' if row is None else 'other_symbol' if row.symbol != symbol else 'not_fetched'
    title = None if row is None else (_filing_title(row) if model is SecFilingInventory else row.title)
    return {'source_type': ref.get('source_type'), 'source_id': str(ref.get('source_id')), 'title': title,
            'category': None, 'reason': reason, 'reason_text': EXCLUSION_REASONS[reason]}
