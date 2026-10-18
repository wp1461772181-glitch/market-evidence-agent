"""Live end-to-end check: a confirmed write reports back in the user's own language.

Asks for a filing in Chinese, presses the card's own button value, and prints only the final
answer and the numbers around it.  Creates one conversation and deletes it afterwards.
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8000"


def env_file(path=".env"):
    values = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def stream(session, payload):
    request = urllib.request.Request(
        BASE + "/agent-chat/stream",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-chat-session": session},
        method="POST",
    )
    events = []
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                events.append([line[7:], ""])
            elif line.startswith("data: ") and events:
                events[-1][1] += line[6:]
    return [(kind, json.loads(body or "{}")) for kind, body in events]


def main():
    session = json.load(open("data/personal-chat-login.json", encoding="utf-8"))["chat_credential"]
    values = env_file()
    config = {"provider": "deepseek", "base_url": "https://api.deepseek.com",
              "model": values.get("DEEPSEEK_MODEL", "deepseek-flash"),
              "api_key": values["DEEPSEEK_API_KEY"]}
    question = "去拉取一下博通的 SEC 数据"

    print("turn 1 ·", question)
    first = stream(session, {**config, "message": question})
    card = next((data for kind, data in first if kind == "clarification"), None)
    if not card:
        print("no card; frames:", [kind for kind, _ in first])
        return 1
    print("  card:", card["display"]["title"], "|", json.dumps(card["display"]["targets"], ensure_ascii=False)[:200])
    conversation = next((data.get("conversation_id") for kind, data in first if data.get("conversation_id")), None)
    confirm = card["choices"][0]["value"]

    print("turn 2 · press", confirm.split(":", 1)[0])
    second = stream(session, {**config, "message": confirm, "conversation_id": conversation,
                              "pending_request_id": card["request_id"], "display_message": "同意"})
    answer = "".join(data.get("text") or "" for kind, data in second if kind == "delta")
    if not answer:
        answer = next((data.get("answer") or "" for kind, data in second if data.get("answer")), "")
    print("\n=== 用户看到的中文回答 ===")
    print(answer.strip() or "(empty)")
    chinese = bool(re.search(r"[\u4e00-\u9fff]", answer))
    named = "博通" in answer or "Broadcom" in answer or "AVGO" in answer
    print("\n汉字:", chinese, "| 说出公司:", named)
    if conversation:
        cleanup(conversation)
    return 0 if chinese and named else 2


def cleanup(conversation):
    """The check is a real turn against the real database: take its conversation back out."""
    from sqlalchemy import create_engine, text
    from app.config import DATABASE_URL
    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        for table in ("agent_messages", "agent_conversation_titles",
                      "agent_conversation_summaries", "agent_consents",
                      "low_confidence_questions"):
            connection.execute(text(f"delete from {table} where conversation_id=:c"), {"c": conversation})
    print("(试跑会话", conversation, "已清理)")


if __name__ == "__main__":
    raise SystemExit(main())
