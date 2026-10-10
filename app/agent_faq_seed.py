from sqlalchemy.dialects.postgresql import insert
from .agent_chat_models import AgentFAQ

FAQ_ROWS = [
    {"question": "研究材料可信度星级是什么？", "answer": "可信度星级是人工对来源可靠性的 1 至 5 星记录；它是审阅判断，不是统计概率。", "category": "可信度"},
    {"question": "市场研究数据的费用是多少？", "answer": "费用取决于所使用的数据与模型服务；本地已保存材料查询不会触发外部抓取。", "category": "费用"},
    {"question": "系统中的预测代表什么？", "answer": "预测是带时间和模型状态的研究记录，不是确定收益或投资建议。", "category": "预测"},
]

def seed_faq(session):
    session.execute(insert(AgentFAQ).values(FAQ_ROWS).on_conflict_do_nothing(index_elements=["question"]))
