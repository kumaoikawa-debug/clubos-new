"""ai_engine 事实回检单元测试（无依赖，纯函数级）。

运行：python -m pytest tests/ai_engine_fact_guard.py -q
      或：python tests/ai_engine_fact_guard.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine import hallucinated_places, restore_brand_names, apply_fact_guard


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


# ---- 2026-10-07 事故回归：品牌名还原把图片 url 里的中文文件名一起改了 ----
# 症状：重新生成一次活动后，C 端详情页所有图片 404。根因是上传文件名里带品牌中文名
# （2026始祖鸟高客_…__image2.jpg），被 restore_brand_names 改成了 ARC'TERYX高客_…。
def test_brand_restore_keeps_url():
    src = "ARC'TERYX × 远拓户外\n本次由始祖鸟品牌支持"
    url = ("/static/uploads/1/7268f757a91c4b2c9bed7021ca1dbd37/extracted/"
           "2026始祖鸟高客_四川新都桥鱼子西方案_最新__image2.jpg")
    assert restore_brand_names(url, src) == url, "图片 url 必须原样保留，改一个字符整页图就 404"
    assert restore_brand_names("始祖鸟冲锋衣", src) == "ARC'TERYX冲锋衣", "正文里的品牌名仍然要还原"
    assert restore_brand_names("https://cdn.x.com/始祖鸟.jpg", src) == "https://cdn.x.com/始祖鸟.jpg"


def test_fact_guard_skips_media_urls():
    src = "ARC'TERYX × 远拓户外 始祖鸟"
    url = "/static/uploads/1/x/2026始祖鸟高客_方案_image2.jpg"
    data = {'activity_master': {'media': [{'ref': 'img_02', 'url': url}], 'title': '始祖鸟专场'},
            'detail': {'blocks': []}}
    apply_fact_guard(data, src)
    assert data['activity_master']['media'][0]['url'] == url, "媒体清单是文件映射，不许做品牌名替换"
    assert data['activity_master']['title'] == "ARC'TERYX专场", "master 里的文案字段仍要还原品牌名"


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
