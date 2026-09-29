#!/usr/bin/env python3
"""Query the balance for this project's configured DeepSeek API key."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_URL = "https://api.deepseek.com/user/balance"


class NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward the bearer credential to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("缺少 python-dotenv，请使用项目虚拟环境运行此脚本。", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or not api_key.strip():
        print("未找到 DEEPSEEK_API_KEY；请检查后端环境变量或项目根目录 .env。", file=sys.stderr)
        return 2

    request = Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
        },
        method="GET",
    )
    opener = build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=20) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        print(f"DeepSeek 余额查询失败：HTTP {exc.code}。密钥未打印。", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"DeepSeek 余额查询失败：网络错误（{type(exc.reason).__name__}）。", file=sys.stderr)
        return 1
    except TimeoutError:
        print("DeepSeek 余额查询失败：请求超时。", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("DeepSeek 返回的内容不是有效 JSON。", file=sys.stderr)
        return 1

    if not isinstance(payload, dict) or not isinstance(payload.get("balance_infos"), list):
        print("DeepSeek 返回了未识别的余额数据格式。", file=sys.stderr)
        return 1

    available = payload.get("is_available")
    if isinstance(available, bool):
        print(f"账户余额是否可用于 API 调用：{'是' if available else '否'}")

    balances = payload["balance_infos"]
    if not balances:
        print("接口没有返回余额明细。")
        return 0

    for item in balances:
        if not isinstance(item, dict):
            continue
        currency = item.get("currency", "未知币种")
        total = item.get("total_balance", "未知")
        granted = item.get("granted_balance", "未知")
        topped_up = item.get("topped_up_balance", "未知")
        print(f"{currency} 可用余额：{total}")
        print(f"  赠送余额：{granted}；充值余额：{topped_up}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
