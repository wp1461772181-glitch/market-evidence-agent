"""The companies this product knows by name: ticker -> the names people write for it.

One table, three readers: the slot reader (``agent_workflow.request_resolution``: which company
the words name), web research (``agent_web_research``: which pages and sentences are about it) and
the evidence gate (``agent_workflow.graph``: whether a page states it).  A name that is not here is
not recognized as a company; the user can always write the ticker itself (SNOW, MU, ...).

Only distinctive names belong here, because the readers match them inside the user's words:
a name that is also an everyday word or part of another phrase would turn ordinary text into a
company (Chinese has no word boundaries: 高通胀 contains 高通, 京东方 contains 京东, 美国银行业
contains 美国银行).  Such a company is listed by the longer, safer name or left out.

The first eight entries keep exactly the names the three old tables held.  Entries are appended;
the order is the order symbols are reported in, so existing questions keep their symbol order.
"""
from __future__ import annotations

COMPANIES: dict[str, tuple[str, ...]] = {
    "AAPL": ("apple", "苹果", "蘋果"),
    "MSFT": ("microsoft", "微软"),
    "NVDA": ("nvidia", "英伟达"),
    "AMD": ("advanced micro devices", "超微", "amd"),
    "GOOGL": ("google", "alphabet", "谷歌"),
    "AMZN": ("amazon", "亚马逊"),
    "TSLA": ("tesla", "特斯拉"),
    "META": ("meta", "脸书"),
    # semiconductors and hardware
    "INTC": ("intel", "英特尔"),
    "QCOM": ("qualcomm", "高通公司"),
    "AVGO": ("broadcom", "博通"),
    "MU": ("micron technology", "micron", "美光"),
    "TSM": ("taiwan semiconductor", "tsmc", "台积电"),
    "ASML": ("asml", "阿斯麦"),
    "AMAT": ("applied materials", "应用材料公司"),
    "LRCX": ("lam research", "泛林"),
    "TXN": ("texas instruments", "德州仪器"),
    "DELL": ("dell technologies", "戴尔"),
    # software and internet
    "ORCL": ("oracle", "甲骨文"),
    "CRM": ("salesforce", "赛富时"),
    "ADBE": ("adobe", "奥多比"),
    "CSCO": ("cisco", "思科"),
    "IBM": ("ibm", "国际商业机器"),
    "NFLX": ("netflix", "奈飞"),
    "PLTR": ("palantir", "帕兰提尔"),
    "SNOW": ("snowflake",),
    "UBER": ("uber", "优步"),
    "ABNB": ("airbnb", "爱彼迎"),
    "PYPL": ("paypal", "贝宝"),
    "SHOP": ("shopify",),
    # consumer and industrial
    "DIS": ("disney", "迪士尼"),
    "BA": ("boeing", "波音"),
    "KO": ("coca-cola", "coca cola", "可口可乐"),
    "PEP": ("pepsico", "百事可乐"),
    "MCD": ("mcdonald's", "mcdonalds", "麦当劳"),
    "NKE": ("nike", "耐克"),
    "SBUX": ("starbucks", "星巴克"),
    "WMT": ("walmart", "沃尔玛"),
    "COST": ("costco", "好市多", "开市客"),
    "HD": ("home depot", "家得宝"),
    "GE": ("general electric", "通用电气"),
    "GM": ("general motors", "通用汽车"),
    # finance, health, energy
    "JPM": ("jpmorgan", "jp morgan", "摩根大通"),
    "GS": ("goldman sachs", "高盛"),
    "MS": ("morgan stanley", "摩根士丹利"),
    "BAC": ("bank of america", "美银"),
    "MA": ("mastercard", "万事达"),
    "AXP": ("american express", "美国运通"),
    "JNJ": ("johnson & johnson", "johnson and johnson", "强生"),
    "PFE": ("pfizer", "辉瑞"),
    "LLY": ("eli lilly", "礼来"),
    "UNH": ("unitedhealth", "联合健康"),
    "XOM": ("exxonmobil", "exxon", "埃克森美孚"),
    "CVX": ("chevron", "雪佛龙"),
    # US-listed Chinese companies
    "BABA": ("alibaba", "阿里巴巴"),
    "PDD": ("pinduoduo", "拼多多"),
    "BIDU": ("baidu", "百度"),
    "NIO": ("蔚来",),
    "XPEV": ("xpeng", "小鹏汽车"),
}

# alias (lower case) -> ticker, in table order: what the slot reader walks.
ALIASES: dict[str, str] = {alias: symbol for symbol, names in COMPANIES.items() for alias in names}
