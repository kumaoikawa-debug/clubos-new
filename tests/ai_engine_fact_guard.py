"""ai_engine 事实回检单元测试（无依赖，纯函数级）。

运行：python -m pytest tests/ai_engine_fact_guard.py -q
      或：python tests/ai_engine_fact_guard.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine import hallucinated_places, restore_brand_names


def _flagged(text, src):
    return hallucinated_places(text, src)


# ---- 假阳性回归：动词短语不该被当成编造地名 ----
def test_verb_phrase_散山():
    # 真实案例：兴福寺活动「一锅暖意，驱散山间微寒」
    out = _flagged("一锅暖意，驱散山间微寒", "暖意山间微寒")
    assert "散山" not in out, f"散山 是动词短语，不应报编造地名: {out}"

def test_verb_phrase_漫山():
    out = _flagged("漫山遍野的杜鹃", "杜鹃")
    assert "漫山" not in out

def test_verb_phrase_环山():
    out = _flagged("沿着环山公路盘旋而上", "公路")
    assert "环山" not in out

def test_relational_suffix_寺中():
    out = _flagged("寺中香火不断，湖畔清风徐来", "香火")
    assert "寺中" not in out and "湖畔" not in out

def test_relational_suffix_村口谷底():
    out = _flagged("村口集合，谷底午餐，坡顶远眺", "集合午餐远眺")
    assert "村口" not in out and "谷底" not in out and "坡顶" not in out

def test_relational_suffix_桥下关外():
    out = _flagged("桥下溪流潺潺，关外草原辽阔", "溪流草原")
    assert "桥下" not in out and "关外" not in out


# ---- 真阳性：原文查无此名的地名应当被报出 ----
def test_real_hallucination_草寺():
    # 原文只提到兴福寺，模型擅自改成草寺庙
    out = _flagged("徒步至草寺庙后森林区域午餐", "兴福寺 森林 午餐")
    assert "草寺" in out, f"草寺 在原文查无此名，应报出: {out}"

def test_real_hallucination_虚构山():
    # 函数只扫「汉字+后缀」两字窗口，故三字节名会命中「构山」这类子串窗口
    out = _flagged("翻过虚构山抵达营地", "营地 翻过")
    assert "构山" in out, f"虚构山 查无此名，应报出其两字窗口: {out}"


# ---- 品牌还原 ----
def test_restore_brand_alias():
    # 模型把 icebreaker 音译/意译成「冰破」→ 还原为资料里的原始写法（小写 canonical）
    text = "使用冰破帐篷更安全"
    src = "我们提供 Icebreaker 帐篷"
    restored = restore_brand_names(text, src)
    assert "icebreaker" in restored, f"应还原为 icebreaker: {restored}"
    assert "冰破" not in restored, f"不应残留音译: {restored}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests)-fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
