"""Run the fixed DEMO MOCK chat cases against a separately started demo API."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx


async def main() -> None:
    port = int(os.environ.get("AGENT_DEMO_API_PORT", "8018"))
    cases = json.loads((Path(__file__).parent.parent / "tests/fixtures/agent_chat_cases.json").read_text())
    async with httpx.AsyncClient(timeout=40) as client:
        for case in cases:
            session = "demo-eval-" + uuid.uuid4().hex * 2
            response = await client.post(
                f"http://127.0.0.1:{port}/agent-chat/stream",
                headers={"X-Chat-Session": session},
                json={"provider":"deepseek","model":"deepseek-chat","api_key":"demo-placeholder-key","message":case["user_text"]},
            )
            if response.status_code != 200:
                raise SystemExit(f"{case['id']}: HTTP {response.status_code}: {response.text[:300]}")
            events=[]; answer=[]
            for block in response.text.split("\n\n"):
                lines=block.splitlines()
                event=next((line[7:] for line in lines if line.startswith("event: ")),None)
                raw=next((line[6:] for line in lines if line.startswith("data: ")),None)
                if not event or not raw: continue
                data=json.loads(raw); events.append(event)
                if event=="delta": answer.append(str(data.get("text","")))
            content="".join(answer)
            if events[-1:] != ["done"] or not content.startswith("DEMO MOCK:"):
                raise SystemExit(f"{case['id']}: expected labelled complete mock answer; got {events[-3:]} {content!r}")
            checks=[]
            for row in case.get("expected_rows",[]) if isinstance(case.get("expected_rows"),list) else []:
                checks.extend((row["date"],str(row["close"])))
            checks.extend(case.get(key) for key in ("expected_filing_date","expected_source") if case.get(key))
            if case["id"]=="faq-fee-exact-hit": checks.append("费用")
            if case["id"]=="faq-price-synonym-literal-miss":
                if "没有匹配" not in content: raise SystemExit(f"{case['id']}: expected literal keyword miss: {content}")
            elif case["id"]=="amd-no-records":
                if "没有匹配" not in content: raise SystemExit(f"{case['id']}: expected empty AMD result: {content}")
            elif any(value not in content for value in checks):
                raise SystemExit(f"{case['id']}: expected {checks}, got {content}")
            print(f"PASS {case['id']}: {content}")


if __name__ == "__main__":
    asyncio.run(main())
