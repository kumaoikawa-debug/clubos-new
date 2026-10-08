"""推文「照抄原文」闸门 + 图片兜底 单元测试（无网络、无 DB）。

背景（2026-10-07 用户反馈「接同样的大模型，写出来的推文还不如豆包/千问/ChatGPT/Gemini」）：
实测把方案原文整段喂给写作模型时，成品 14.5% 的 8 字片段能在原文里逐字命中，
最长一段连续 31 字与原文一字不差 —— 读起来就是「PPT 景点说明的散文翻译」。
提示词约束不住，所以在出口加了可断言的机械闸门。

运行：python -m pytest tests/ai_engine_echo_guard.py -q
      或：python tests/ai_engine_echo_guard.py
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine import (longest_common_span, source_echo_blocks, _ensure_media_refs,
                       _fact_digest, normalize_channel_blocks)

SRC = ("娜姆湖度假酒店坐落于毕棚沟景区内娜姆湖畔，是景区内稀缺的度假居所，"
       "藏羌碉楼风格别墅群落隐匿于原始森林之间。酒店海拔约2900米，客房配备24小时全屋地暖。"
       "雷神王瀑布坐落于理小路36km处，海拔约3350米，隐匿于原始冷杉林间。")


# ---------------- longest_common_span ----------------

def test_lcs_finds_verbatim_copy():
    copied = "娜姆湖度假酒店坐落于毕棚沟景区内娜姆湖畔"
    assert longest_common_span(copied, SRC) >= 18


def test_lcs_ignores_punctuation():
    # 只换标点不算重写 —— 归一化后仍然命中
    assert longest_common_span("娜姆湖度假酒店，坐落于毕棚沟景区内", SRC) >= 15


def test_lcs_small_for_real_rewrite():
    rewritten = ("今晚就住在景区里面，车能一直开到酒店门口。房子是藏羌碉楼的样子，"
                 "散在林子里，夜里安静得只听见水声。")
    assert longest_common_span(rewritten, SRC) <= 8, "真正的重写不该与原文长距离重合"


def test_lcs_empty_input():
    assert longest_common_span('', SRC) == 0
    assert longest_common_span('随便写点', '') == 0


# ---------------- source_echo_blocks ----------------

def test_echo_flags_copied_block_only():
    blocks = [
        {'type': 'lead', 'body': '车开到酒店门口，行李不用再搬第二趟。'},
        {'type': 'narrative', 'headline': '娜姆湖度假酒店',
         'body': '娜姆湖度假酒店坐落于毕棚沟景区内娜姆湖畔，是景区内稀缺的度假居所。'},
    ]
    bad = source_echo_blocks(blocks, SRC)
    assert bad == [1], f"只应标记照抄的那一块，实际 {bad}"


def test_echo_clean_doc_passes():
    blocks = [
        {'type': 'narrative', 'headline': '住进景区里，少开四十分钟回头路',
         'body': '今晚的落脚点在景区里面。车能一直开到门口，早上不用再排队进沟。'},
        {'type': 'facts', 'body': '时间：2026年10月xx日\n住宿：景区内度假酒店'},
    ]
    assert source_echo_blocks(blocks, SRC) == []


def test_echo_no_source_never_flags():
    blocks = [{'type': 'narrative', 'body': '任意内容' * 20}]
    assert source_echo_blocks(blocks, '') == []


def test_echo_deep_flags_copied_fact_value():
    """用户反馈的那一类：详情要点表里照搬方案原文的参数串（2026-10-09）。"""
    blocks = [{'type': 'facts',
               'items': [{'label': '活动形式', 'value': '徒步 + 颂钵冥想 + 自然拓染'}]}]
    src = '活动形式：徒步+颂钵冥想+自然拓染，集合点见方案'
    assert source_echo_blocks(blocks, src, deep=True) == [0], '要点表照抄必须被标出'
    assert source_echo_blocks(blocks, src) == [], '浅查不该碰要点表（渠道那边只改正文）'


def test_echo_deep_ignores_short_fact_values():
    """日期/里程这类短事实本来就该与资料一字不差，不能判成照抄去白跑一次重写。"""
    blocks = [{'type': 'facts', 'items': [{'label': 'DATE', 'value': '2026-10-24'}]}]
    assert source_echo_blocks(blocks, '出发日期 2026-10-24 早上集合', deep=True) == []


def test_echo_deep_skips_fact_like_values():
    """价格 / 费用清单属于事实枚举（费用包含什么不能编），不参与照抄判定。"""
    src = '费用包含：全程车费、午餐、颂钵课程、拓染材料、领队服务及保险。'
    blocks = [{'type': 'facts', 'items': [{'label': '费用包含', 'value': src}]}]
    assert source_echo_blocks(blocks, src, deep=True) == []
    price = [{'type': 'facts', 'items': [{'label': '价格', 'value': '新客498元/人，会员可用1500积分兑换。'}]}]
    assert source_echo_blocks(price, '新客498元/人，会员可用1500积分兑换。', deep=True) == []


# ---------------- _ensure_media_refs ----------------

def test_media_refs_filled_when_model_gave_none():
    blocks = [{'type': 'lead', 'body': '开场'},
              {'type': 'narrative', 'headline': '第一节', 'body': '正文'},
              {'type': 'narrative', 'headline': '第二节', 'body': '正文'},
              {'type': 'facts', 'body': '活动信息'}]
    out = _ensure_media_refs(blocks, ['img_02', 'img_03', 'img_04'])
    got = [b.get('mediaRefs') for b in out]
    assert got == [['img_02'], ['img_03'], ['img_04'], None], f"铺图结果不对: {got}"


def test_media_refs_left_alone_when_present():
    blocks = [{'type': 'narrative', 'body': '正文', 'mediaRefs': ['img_09']}]
    out = _ensure_media_refs(blocks, ['img_02', 'img_03'])
    assert out[0]['mediaRefs'] == ['img_09'], "模型已经给了图，不要再插手"


def test_media_refs_no_photo_noop():
    blocks = [{'type': 'narrative', 'body': '正文'}]
    assert _ensure_media_refs(blocks, []) == blocks


# ---------------- _fact_digest 兜底 ----------------

def test_fact_digest_empty_source_returns_empty_without_calling_gateway():
    assert asyncio.run(_fact_digest(1, '')) == ''
    assert asyncio.run(_fact_digest(1, '   ')) == ''


# ---------------- 回归：结构归一化 ----------------

def test_normalize_still_saves_non_block_shape():
    data = {'title': 'T', 'narrative': [{'headline': 'H', 'body': 'B'}]}
    out = normalize_channel_blocks(data)
    assert isinstance(out.get('blocks'), list) and out['blocks'], "自创结构必须被救回 blocks"


if __name__ == '__main__':
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_')]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f'PASS  {name}')
        except AssertionError as e:
            bad += 1
            print(f'FAIL  {name}: {e}')
        except Exception as e:
            bad += 1
            print(f'ERROR {name}: {type(e).__name__}: {e}')
    print(f'\n{len(fns) - bad}/{len(fns)} passed')
    sys.exit(1 if bad else 0)
