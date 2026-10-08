"""facts / info 的「body → items」结构兜底单测（2026-10-09）。

背景：实测 qwen3-max 会把要点表写成一段多行文本塞进 body，而 C 端 facts / info
只认 items —— 页面上于是出现一个**空白块**，内容整块消失。这里锁住兜底行为。

运行：python tests/block_normalize_fallback.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine import _sanitize_blocks


def test_facts_body_becomes_items():
    out = _sanitize_blocks([{'type': 'facts',
                             'body': '路线：石笋寺 → 兴福寺\n餐饮：户外牛肉汤锅（含黄牛腱肉、虾滑、肥牛）'}])
    b = out[0]
    assert len(b.get('items') or []) == 2, out
    assert b['items'][0]['label'] == '路线' and '石笋寺' in b['items'][0]['value'], out
    assert 'body' not in b, '同一份内容不该在 body 里再留一遍'


def test_info_body_becomes_items_with_title():
    out = _sanitize_blocks([{'type': 'info', 'headline': '费用说明',
                             'body': '费用包含：车费、午餐\n费用不含：个人消费'}])
    b = out[0]
    assert b.get('items') == ['费用包含：车费、午餐', '费用不含：个人消费'], out
    assert b.get('title') == '费用说明', 'info 的标题前端读 title，不能丢'


def test_existing_items_untouched():
    out = _sanitize_blocks([{'type': 'facts', 'items': [{'label': 'A', 'value': 'B'}]}])
    assert out[0]['items'] == [{'label': 'A', 'value': 'B'}]


if __name__ == '__main__':
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_')]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print('PASS ', name)
        except AssertionError as e:
            bad += 1
            print('FAIL ', name, e)
    print(f'\n{len(fns)-bad}/{len(fns)} passed')
    sys.exit(1 if bad else 0)
