from app.agent_workflow.language import format_answer

def test_simplified_body_preserves_original_title_and_url():
    title='臺灣產品營運報告';url='https://example.org/產品?名稱=營運'
    result=format_answer(f'該公司發布新產品，營運團隊已出貨。來源：{title}，{url} [1]','总结最新消息',[{'title':title}])
    assert '该公司发布新产品，营运团队已出货' in result
    assert title in result and url in result

def test_english_request_preserves_source_language():
    assert format_answer('發布產品','Summarize',[])=='發布產品'
