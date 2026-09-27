"""回归测试：出行清单 × 商城在售装备的推荐引擎。

锁住的不变量：
  1. 候选只能是调用方传入的**真实在售商品**，引擎不会生成或补全商品条目；
  2. 清单项没有精确匹配时给**同类平替**：必须带 substitute 标记与说明文案，
     且 matched 仍为 False（平替不冒充精确匹配）；实在没有相近品类才如实说「暂无」；
  3. 中文歧义单字不误判——「防水防滑徒步鞋」不能被当成饮水需求（否则会推水壶）；
  4. 同一清单项匹配多件时按契合度排序、数量有上限；
  5. 生产模式下 C 端详情接口必须放行 gearRecommendations，否则线上会被静默过滤掉。

全程不调用大模型、不联网、不碰业务库。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, '.')

# 自我隔离：下面要 import app，而 app.py 在模块级就调 init_db()，
# 不指库就会把迁移落到项目真库 clubos.db（未被 git 跟踪、也没有备份）。
os.environ['CLUBOS_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='clubos-gear-smoke-'), 'clubos.db')
os.environ['CLUBOS_SECURITY_MODE'] = 'demo'
os.environ['COMMERCE_PROVIDER'] = 'local'
for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
    os.environ.pop(_k, None)

import db  # noqa: E402

db.init_db()

from clubos_domain.gear_recommend import GearRecommendService  # noqa: E402

FAILED = []


def check(name, cond, detail=''):
    print(('  OK   ' if cond else '  FAIL ') + name + (('  | ' + str(detail)) if detail else ''))
    if not cond:
        FAILED.append(name)


def product(pid, name, category, price=100.0, stock=10):
    return {'id': pid, 'name': name, 'price': price, 'stock': stock,
            'category': category, 'image_url': ''}


# 线上现状：商城在售 4 件装备（与 demo 播种一致）
CATALOG = [
    product(1, '轻量防风软壳', '服装', 699),
    product(2, '碳纤维折叠登山杖', '徒步装备', 299),
    product(3, '22L 日行背包', '背包', 459),
    product(4, '美丽诺羊毛基础层', '服装', 599),
]
svc = GearRecommendService()


def plan(master, catalog=None):
    return svc.plan(CATALOG if catalog is None else catalog, master).as_dict()


def item_of(p, text):
    return next((i for i in p['items'] if i['text'] == text), None)


print('== 1. 清单 → 装备匹配 ==')
p = plan({'checklist': ['单日小背包'], 'title': '蓥华山徒步'})
it = item_of(p, '单日小背包')
check('背包清单匹配到背包商品', bool(it and it['matched']),
      it['matches'][0]['name'] if it and it['matches'] else '（无）')
check('推荐理由写清了契合的品类', bool(it and it['matches'][0]['reason'].startswith('契合')),
      it['matches'][0]['reason'] if it and it['matches'] else '')

print('== 2. 候选只能来自真实在售商品 ==')
shown = {m['name'] for i in p['items'] for m in i['matches']} | {e['name'] for e in p['extras']}
check('推荐里出现的商品都来自传入的候选集', shown <= {c['name'] for c in CATALOG}, shown)

print('== 3. 没有精确匹配时给同类平替，且平替不冒充精确匹配 ==')
p = plan({'checklist': ['防晒用品'], 'title': '徒步'})
it = item_of(p, '防晒用品')
check('防晒用品未精确匹配（商城确实没有防晒件）', bool(it and not it['matched']),
      it['matches'] if it else None)
check('认出了防晒品类', bool(it and it['tags'] == ['sun']), it['tags'] if it else None)
check('清单原文原样保留', bool(it and it['text'] == '防晒用品'))
# sun → clothing 的替代关系：防风软壳 / 羊毛基础层都算相近衣物
subs = it['substitutes'] if it else []
check('给了平替（衣物面料类）', bool(subs) and subs[0]['substitute'] is True,
      [(s['name'], s['reason']) for s in subs])
check('平替说明写清「暂无 + 相近品类」', bool(subs) and '暂无' in subs[0]['reason'] and '相近' in subs[0]['reason'],
      subs[0]['reason'] if subs else '')
check('coverage.substituted 计数正确', p['coverage'].get('substituted') == 1, p['coverage'])
check('平替不再出现在其他在售装备里', all(e['id'] != subs[0]['id'] for e in p['extras']),
      [e['name'] for e in p['extras']])

print('== 3b. 平替按替代关系排序：鞋袜缺货优先推徒步支撑而非衣物 ==')
p = plan({'checklist': ['防水防滑徒步鞋'], 'title': '徒步'})
it = item_of(p, '防水防滑徒步鞋')
subs = it['substitutes'] if it else []
check('鞋袜的平替是徒步支撑（登山杖）', bool(subs) and subs[0]['name'] == '碳纤维折叠登山杖',
      [(s['name'], s['reason']) for s in subs])

print('== 3c. 完全没有相近品类时仍如实说「暂无」 ==')
ONLY_PACK = [product(3, '22L 日行背包', '背包', 459)]
p = plan({'checklist': ['防晒用品']}, ONLY_PACK)
it = item_of(p, '防晒用品')
check('背包替代不了防晒 → 无平替', bool(it and not it['matched'] and not it['substitutes']),
      it if it else None)

print('== 3d. 有精确匹配的清单项不产生平替 ==')
p = plan({'checklist': ['单日小背包', '防晒用品']})
for text in ('单日小背包', '防晒用品'):
    it = item_of(p, text)
    want_subs = text == '防晒用品'
    check('%s 的平替仅在无精确匹配时出现' % text,
          bool(it) and bool(it['substitutes']) == want_subs,
          (it and [s['name'] for s in it['substitutes']]))

print('== 4. 歧义单字回归：防水鞋 ≠ 饮水需求 ==')
p = plan({'checklist': ['防水防滑徒步鞋'], 'title': '徒步'})
it = item_of(p, '防水防滑徒步鞋')
check('只识别为鞋类，不误判为饮水', bool(it and it['tags'] == ['footwear']), it['tags'] if it else None)

print('== 5. 商城品类扩展后能正确匹配（泛化能力）==')
WIDE = CATALOG + [
    product(5, '户外保温水壶 750ml', '水具', 129),
    product(6, '清爽防晒乳 SPF50+', '护肤', 99),
    product(7, '便携头灯 400流明', '照明', 159),
    product(8, '可降解垃圾袋（20只）', '环保', 19),
]
p = plan({'checklist': ['携带个人饮用水', '防晒用品', '夜间行进需头灯', '垃圾袋（用于环保收集）']}, WIDE)
for text, want in (('携带个人饮用水', '户外保温水壶 750ml'),
                   ('防晒用品', '清爽防晒乳 SPF50+'),
                   ('夜间行进需头灯', '便携头灯 400流明'),
                   ('垃圾袋（用于环保收集）', '可降解垃圾袋（20只）')):
    it = item_of(p, text)
    got = it['matches'][0]['name'] if it and it['matches'] else '（无）'
    check('%s → %s' % (text, want), got == want, got)

print('== 6. 排序与限量 ==')
p = plan({'checklist': ['穿着适合徒步的衣物与鞋子']})
it = item_of(p, '穿着适合徒步的衣物与鞋子')
check('同品类多件时不超过每项上限', len(it['matches']) <= svc.max_per_item, len(it['matches']))
check('优先推名称直接命中品类的商品',
      bool(it['matches']) and it['matches'][0]['name'] in ('轻量防风软壳', '美丽诺羊毛基础层'),
      [m['name'] for m in it['matches']])

print('== 7. 商城无在售商品时不编造 ==')
p = plan({'checklist': ['轻便背包']}, [])
check('available=False', p['available'] is False)
check('清单项保留但没有推荐', bool(item_of(p, '轻便背包')) and not item_of(p, '轻便背包')['matched'])
check('extras 为空', p['extras'] == [])

print('== 8. 无清单时给出活动级推荐（活动语境只影响排序）==')
MIXED = CATALOG[:3] + [product(9, '六角天幕', '露营', 1299)]
p = plan({'checklist': [], 'title': '孟屯河谷徒步', 'location': '理县'}, MIXED)
check('items 为空', p['items'] == [])
check('extras 覆盖全部在售装备', len(p['extras']) == len(MIXED), [e['name'] for e in p['extras']])
check('与徒步语境无关的装备排在最后', p['extras'][-1]['name'] == '六角天幕',
      [e['name'] for e in p['extras']])
p = plan({'checklist': []}, CATALOG + [product(9, '六角天幕', '露营', 1299)])
check('extras 有数量上限', len(p['extras']) == svc.max_extras, len(p['extras']))

print('== 9. C 端详情接口必须放行 gearRecommendations（生产白名单）==')
import app  # noqa: E402

with app.conn() as c:
    c.execute("UPDATE activities SET status='published', activity_master_json=? WHERE id=1",
              (json.dumps({'title': '测试徒步', 'checklist': ['轻便背包', '防晒用品'],
                           'itinerary': [], 'fees': {}, 'services': [], 'media': []},
                          ensure_ascii=False),))
    c.execute("UPDATE clubs SET status='active' WHERE id=1")

app.IS_PROD = True
try:
    r = app.public_activity(1)
    check('生产模式返回 gearRecommendations', 'gearRecommendations' in r, sorted(r.keys()))
    check('生产模式未泄露 source_json', 'source_json' not in r)
    gr = r.get('gearRecommendations') or {}
    check('推荐数据来自真实在售商品', gr.get('catalogCount', 0) >= 1, gr.get('catalogCount'))
    check('生产模式下推荐条目仍然可用', bool(gr.get('items')), [i['text'] for i in gr.get('items') or []])
finally:
    app.IS_PROD = False

print()
if FAILED:
    print('失败 %d 项：%s' % (len(FAILED), FAILED))
    sys.exit(1)
print('全部通过')
