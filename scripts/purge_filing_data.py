"""Remove the SEC archive and everything derived from it before the full-text refetch.

Scope (confirmed with the operator):
  * every ``sec_filing_inventory`` row,
  * knowledge documents/chunks/tasks for ``official_filing`` and ``ai_analysis``,
  * ``material_analysis_*`` rows produced from SEC filings (uploaded-media analyses stay),
  * the 824 ``historical_research`` replay forecasts and their evaluations/evidence,
  * market prices and revisions for the four symbols that leave the universe
    (AVGO/AMD/INTC/MU); SPY stays because it is the benchmark,
  * SEC monitor run history.

Kept: the 14 ``observed`` jobs and their versions, the 8 uploaded-media analyses,
uploaded evidence, conversations, web sources and FAQ.

Run with ``--dry-run`` first; the script prints each step's row count either way and
commits nothing in dry-run mode.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

from app.config import DATABASE_URL

DROP_SYMBOLS = ("AVGO", "AMD", "INTC", "MU")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    engine = create_engine(DATABASE_URL)
    steps: list[tuple[str, str]] = [
        # --- forecast side -------------------------------------------------
        (
            "forecast_evaluations_v2 (replay)",
            "delete from forecast_evaluations_v2 where forecast_version_id in "
            "(select id from forecast_versions_v2 where job_id in "
            "(select id from forecast_jobs_v2 where time_mode = 'historical_research'))",
        ),
        (
            "forecast_jobs_v2 detach (replay)",
            "update forecast_jobs_v2 set result_version_id = null, root_version_id = null, "
            "parent_version_id = null where time_mode = 'historical_research'",
        ),
        (
            "forecast_versions_v2 children (replay)",
            "delete from forecast_versions_v2 where parent_version_id is not null and job_id in "
            "(select id from forecast_jobs_v2 where time_mode = 'historical_research')",
        ),
        (
            "forecast_versions_v2 roots (replay)",
            "delete from forecast_versions_v2 where job_id in "
            "(select id from forecast_jobs_v2 where time_mode = 'historical_research')",
        ),
        (
            "forecast_jobs_v2 (replay)",
            "delete from forecast_jobs_v2 where time_mode = 'historical_research'",
        ),
        # --- material analysis side ---------------------------------------
        (
            "material_analysis_jobs detach",
            "update material_analysis_jobs set result_analysis_id = null "
            "where result_analysis_id is not null",
        ),
        (
            "material_analysis_versions detach (official_filing)",
            "update material_analysis_versions set previous_version_id = null "
            "where source_type = 'official_filing'",
        ),
        (
            "material_analysis_versions (official_filing)",
            "delete from material_analysis_versions where source_type = 'official_filing'",
        ),
        (
            "material_analysis_jobs (orphaned)",
            "delete from material_analysis_jobs where id not in "
            "(select job_id from material_analysis_versions where job_id is not null)",
        ),
        # --- evidence side -------------------------------------------------
        (
            "evidence_event_versions_v2 (unreferenced)",
            "__EVIDENCE__",
        ),
        # --- knowledge side ------------------------------------------------
        (
            "knowledge_index_tasks (official_filing/ai_analysis)",
            "delete from knowledge_index_tasks where chunk_id in "
            "(select c.id from knowledge_chunks c join knowledge_documents d on d.id = c.document_id "
            " where d.source_type in ('official_filing','ai_analysis'))",
        ),
        (
            "knowledge_chunks (official_filing/ai_analysis)",
            "delete from knowledge_chunks where document_id in "
            "(select id from knowledge_documents where source_type in ('official_filing','ai_analysis'))",
        ),
        (
            "knowledge_documents (official_filing/ai_analysis)",
            "delete from knowledge_documents where source_type in ('official_filing','ai_analysis')",
        ),
        (
            "knowledge_extraction_runs",
            "delete from knowledge_extraction_runs",
        ),
        # --- filings and market data ---------------------------------------
        ("sec_filing_inventory", "delete from sec_filing_inventory"),
        ("official_monitor_runs_v2", "delete from official_monitor_runs_v2"),
        (
            "market_price_revisions (dropped symbols)",
            "delete from market_price_revisions where symbol in ('AVGO','AMD','INTC','MU')",
        ),
        (
            "market_prices (dropped symbols)",
            "delete from market_prices where symbol in ('AVGO','AMD','INTC','MU')",
        ),
    ]
    with engine.begin() as conn:
        for label, statement in steps:
            if statement == "__EVIDENCE__":
                kept: set[str] = set()
                for row in conn.execute(
                    text(
                        "select evidence_version_id from material_analysis_versions "
                        "where evidence_version_id is not null"
                    )
                ):
                    kept.add(str(row[0]))
                for row in conn.execute(
                    text(
                        "select jsonb_array_elements(evidence_version_manifest) as item "
                        "from forecast_versions_v2 "
                        "where jsonb_typeof(evidence_version_manifest) = 'array'"
                    )
                ):
                    item = row[0] or {}
                    if isinstance(item, dict) and item.get("id"):
                        kept.add(str(item["id"]))
                conn.execute(
                    text(
                        "update evidence_event_versions_v2 set previous_version_id = null "
                        "where previous_version_id is not null and previous_version_id <> all(:kept)"
                    ),
                    {"kept": sorted(kept)},
                )
                result = conn.execute(
                    text("delete from evidence_event_versions_v2 where id <> all(:kept)"),
                    {"kept": sorted(kept)},
                )
                print(f"{label:<52} {result.rowcount:>7} rows  (kept {len(kept)})")
                continue
            result = conn.execute(text(statement))
            print(f"{label:<52} {result.rowcount:>7} rows")
        remaining = {
            "forecast_versions_v2": conn.execute(text("select count(*) from forecast_versions_v2")).scalar(),
            "forecast_jobs_v2": conn.execute(text("select count(*) from forecast_jobs_v2")).scalar(),
            "material_analysis_versions": conn.execute(
                text("select count(*) from material_analysis_versions")
            ).scalar(),
            "evidence_event_versions_v2": conn.execute(
                text("select count(*) from evidence_event_versions_v2")
            ).scalar(),
            "knowledge_documents": conn.execute(text("select count(*) from knowledge_documents")).scalar(),
            "knowledge_chunks": conn.execute(text("select count(*) from knowledge_chunks")).scalar(),
            "sec_filing_inventory": conn.execute(text("select count(*) from sec_filing_inventory")).scalar(),
            "market_prices": conn.execute(text("select count(*) from market_prices")).scalar(),
        }
        print("\nremaining:", remaining)
        if dry_run:
            raise SystemExit("dry-run: rolled back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
