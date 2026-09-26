"""回归测试：live 模式下 master.media 必须保留 url。

背景：ai_engine.generate_activity 的 live 分支曾直接把模型输出当 master 用，
模型只会写 ["img_01",...] 这种纯 ref，于是前端 ref→url 映射为空，
整页图片渲染成灰色占位块、头图退化成纯色。此测试用假网关锁住这条不变量。
"""
import asyncio, os, sys, tempfile
sys.path.insert(0, '.')

# 自我隔离：本测试会走到 generate_activity 的 mock 分支 → record_mock_usage() 会往库里插
# ai_usage_records 一行。必须在 import 之前把库路径指向临时文件，确保绝不碰项目真库 clubos.db
# （它是仓库里唯一没被 git 跟踪、也没有备份的库）。
os.environ['CLUBOS_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='clubos-media-smoke-'), 'clubos.db')

import db
db.init_db()          # 临时库要先建表，否则 mock usage 落不进去

import ai_engine

SOURCE = {
    'text': '10月1日 跟我们一起进山、去理县孟屯河谷体验一场LNT环保徒步之旅，早上8点天府广场集合，限20人，每人198元。',
    'files': [],
    'images': [
        {'ref': 'img_01', 'name': 'IMG_3547.jpeg', 'url': '/static/uploads/1/b/IMG_3547.jpeg', 'width': 1920, 'height': 1280, 'orientation': 'landscape'},
        {'ref': 'img_02', 'name': 'IMG_3546.jpeg', 'url': '/static/uploads/1/b/IMG_3546.jpeg', 'width': 960, 'height': 1440, 'orientation': 'portrait'},
    ],
}
SOURCE['media_manifest'] = [
    {'ref': 'img_01', 'name': 'IMG_3547.jpeg', 'url': '/static/uploads/1/b/IMG_3547.jpeg'},
    {'ref': 'img_02', 'name': 'IMG_3546.jpeg', 'url': '/static/uploads/1/b/IMG_3546.jpeg'},
]

MODEL_JSON = {
    'activity_master': {
        'title': '10月1日｜孟屯河谷 LNT 无痕徒步', 'date': '10月1日', 'location': '理县孟屯河谷',
        'price': 198, 'capacity': 20, 'publicFacts': {}, 'itinerary': [], 'fees': {}, 'checklist': [],
        'services': [], 'internalData': {}, 'uncertainties': [], 'blocking_conflicts': [],
        # 模型「创作」出来的媒体清单：只有 ref，没有 url —— 正是线上事故的形态
        'media': ['img_01', 'img_02', 'img_99'],
    },
    'detail': {
        'activityUnderstanding': 'x', 'coreSellingIdea': 'y', 'editorialIntent': {},
        'blocks': [{'type': 'hero', 'headline': 'H', 'mediaRefs': ['img_01']},
                   {'type': 'gallery', 'mediaRefs': ['img_02']}],
    },
}


class FakeGW:
    def __init__(self, data): self.data = data


def run(live: bool):
    async def fake(**kw):
        return FakeGW(MODEL_JSON) if live else None
    ai_engine.generate_json = fake
    return asyncio.run(ai_engine.generate_activity(1, SOURCE))


fails = []
def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (f'  [{detail}]' if detail else ''))
    if not cond: fails.append(name)

print('=== live 模式（模型回写纯 ref）===')
data, _ = run(True)
med = data['activity_master']['media']
print('  实际写出的 media:', [(x.get('ref'), bool(x.get('url'))) for x in med])
check('media 每一项都是带 url 的对象', all(isinstance(x, dict) and x.get('url') for x in med))
check('只保留真实存在的图（编造的 img_99 被剔除）', [x['ref'] for x in med] == ['img_01', 'img_02'])
check('url 指向真实上传路径', med[0]['url'] == '/static/uploads/1/b/IMG_3547.jpeg')
check('宽度等信息一并保留（排版要用）', med[0].get('width') == 1920)

print('=== mock 模式（离线兜底）===')
data2, _ = run(False)
med2 = data2['activity_master']['media']
print('  实际写出的 media:', [(x.get('ref'), bool(x.get('url'))) for x in med2])
check('mock 模式同样带 url', all(isinstance(x, dict) and x.get('url') for x in med2))
check('mock 模式图片数量正确', len(med2) == 2)

print('=== media_catalog 边界 ===')
check('空 source 不报错', ai_engine.media_catalog({}) == [])
check('已带 url 的旧 master 条目被保留',
      [x['ref'] for x in ai_engine.media_catalog({}, {'media': [{'ref': 'img_07', 'url': '/static/demo/a.png'}]})] == ['img_07'])
check('无 url 的旧条目被剔除（留着只会变灰块）',
      ai_engine.media_catalog({}, {'media': [{'ref': 'img_07'}]}) == [])

print()
print('RESULT:', 'ALL PASS' if not fails else f'{len(fails)} FAILED -> {fails}')
sys.exit(1 if fails else 0)
