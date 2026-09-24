#!/usr/bin/env python3
"""mock 与真实大模型的「活动生成」质量对照实验。

同一批活动原始资料，分别打到：
  --mock-url  : MOCK_AI=1 的实例（当前 demo 行为）
  --live-url  : MOCK_AI=0 且已配置真实 Provider 的实例（DeepSeek / 通义千问）

对比维度（全部可从响应 JSON 客观计算，不靠主观印象）：
  1. 结构完整度    block 数、block 类型覆盖、必需字段是否齐全
  2. 事实保真      简报里的日期/价格/人数/地点是否出现在产出中（"允许创造表达，不允许创造事实"）
  3. 跨活动多样性  不同活动的标题是否雷同、两两文本相似度（雷同 = 模板化）
  4. 占位符残留    是否命中 mock 兜底话术（如"周末自然计划"、"根据真实资料提炼"）
  5. 内容深度      产出总字符数
  6. 成本观测      provider / model / tokens / 平台成本（来自 ai_usage_records）

用法：
  # 只跑 mock（无需 Key，先量化现状）
  python scripts/compare_mock_vs_live.py --mock-url http://127.0.0.1:8000 --club 1

  # mock vs live 完整对照（需 Key + MOCK_AI=0 实例）
  python scripts/compare_mock_vs_live.py \
      --mock-url http://127.0.0.1:8000 \
      --live-url http://127.0.0.1:8002 \
      --db /tmp/clubos_staging/clubos.db \
      --out /path/to/报告.md
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import httpx

# 同一批"活动原始资料"，刻意覆盖：mock 的已知分支 / 完全新场景 / 长短不一 / 有无明确价格。
BRIEFS: list[dict] = [
    {
        'id': 'A-已知分支-蓥华山瑜伽',
        'prompt': '10月24日带30个会员去蓥华山，上午户外瑜伽，下午6公里轻徒步，人均198元。',
        'expect': ['蓥华山', '198'],
    },
    {
        'id': 'B-已知分支-鱼子西',
        'prompt': '川西两天一夜，新都桥+鱼子西，雪山观景，住精品酒店，20人小团，1280元每人。',
        'expect': ['鱼子西', '1280'],
    },
    {
        'id': 'C-已知分支-皮划艇露营',
        'prompt': '周末千岛湖皮划艇+湖畔露营，2天1夜，16人，880元含装备与两餐。',
        'expect': ['千岛湖', '880'],
    },
    {
        'id': 'D-新场景-青城山登山',
        'prompt': '2026年10月25日青城山秋日登山，20人，298元每人，含往返大巴和午餐，早上7点半春熙路集合，全程约8公里，适合新手。',
        'expect': ['青城山', '298', '20'],
    },
    {
        'id': 'E-新场景-亲子农场',
        'prompt': '11月2日亲子农场日，带孩子摘菜做手工，25组家庭，198元每组。',
        'expect': ['198'],
    },
    {
        'id': 'F-新场景-深圳海岸线穿越',
        'prompt': '12月6日深圳大鹏半岛海岸线穿越，40人，168元每人，含往返包车，全程12公里，强度中等。',
        'expect': ['大鹏', '168', '40'],
    },
    {
        'id': 'G-新场景-雨崩徒步6日',
        'prompt': '云南雨崩徒步6日，16人精品小团，3980元每人，含丽江往返、4晚住宿、专业领队与保险。',
        'expect': ['雨崩', '3980'],
    },
    {
        'id': 'H-新场景-城市夜骑',
        'prompt': '本周五晚8点成都城市夜骑，锦江绿道约25公里，30个名额，免费参加，需自带头盔。',
        'expect': ['成都', '25'],
    },
]

PLACEHOLDER_TITLES = {'周末自然计划'}
PLACEHOLDER_PHRASES = [
    '根据真实资料提炼', '不用统一套', 'AI应根据照片自己判断',
    '根据真实活动资料里最值得出发的理由', '一场需要从现有素材中提炼核心动机',
]


def _client(base_url: str, *, username: str = '', password: str = '',
            insecure: bool = False) -> httpx.Client:
    c = httpx.Client(base_url=base_url, timeout=300, follow_redirects=False,
                     trust_env=False, verify=not insecure)
    if username:
        r = c.post('/api/auth/login', json={'username': username, 'password': password})
        if r.status_code != 200:
            raise SystemExit(f'登录失败 ({r.status_code}): {r.text[:160]}')
    return c


def _generate(c: httpx.Client, club: int, prompt: str) -> tuple[dict | None, str]:
    try:
        r = c.post(f'/api/club/{club}/activities/ai-generate', data={'prompt': prompt})
    except Exception as exc:  # noqa: BLE001
        return None, f'请求异常: {exc}'
    if r.status_code != 200:
        try:
            err = r.json().get('detail') or r.text
        except Exception:  # noqa: BLE001
            err = r.text
        return None, f'HTTP {r.status_code}: {str(err)[:200]}'
    try:
        return r.json(), ''
    except Exception as exc:  # noqa: BLE001
        return None, f'响应非 JSON: {exc}'


def _blob(out: dict) -> str:
    d = out.get('detail') or {}
    m = out.get('activityMaster') or {}
    parts = [str(m.get('title') or ''), str(d.get('coreSellingIdea') or ''),
             str(d.get('activityUnderstanding') or '')]
    for b in (d.get('blocks') or []):
        parts.append(json.dumps(b, ensure_ascii=False))
    return '\n'.join(parts)


def analyze(out: dict, expect: list[str]) -> dict:
    m = out.get('activityMaster') or {}
    d = out.get('detail') or {}
    blocks = d.get('blocks') or []
    types = [str(b.get('type') or '') for b in blocks]
    blob = _blob(out)
    full = json.dumps(out, ensure_ascii=False)
    missing_required = [k for k in ('activityUnderstanding', 'coreSellingIdea', 'blocks') if not d.get(k)]
    placeholder = [p for p in PLACEHOLDER_PHRASES if p in full]
    return {
        'title': str(m.get('title') or ''),
        'blocks': len(blocks),
        'types': sorted(set(t for t in types if t)),
        'dup_types': sorted({t for t in types if t and types.count(t) > 1}),
        'chars': len(full),
        'blob': blob,
        'missing_required': missing_required,
        'placeholder': placeholder,
        'default_title': str(m.get('title') or '') in PLACEHOLDER_TITLES,
        'fact_hits': [t for t in expect if t in full],
        'fact_total': len(expect),
    }


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _pairwise_mean(blobs: list[str]) -> float:
    if len(blobs) < 2:
        return 0.0
    vals = []
    for i in range(len(blobs)):
        for j in range(i + 1, len(blobs)):
            vals.append(_similarity(blobs[i], blobs[j]))
    return sum(vals) / len(vals)


def _usage(db: str | None, providers: list[str]) -> list[dict]:
    if not db or not Path(db).exists():
        return []
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        marks = ','.join('?' for _ in providers)
        cur = con.execute(
            f'SELECT task_type,provider,model,status,input_tokens,output_tokens,provider_cost,created_at '
            f'FROM ai_usage_records WHERE provider IN ({marks}) ORDER BY id DESC LIMIT 60',
            providers,
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return []
    finally:
        con.close()


def run_arm(label: str, base_url: str, club: int, *, username: str, password: str,
            insecure: bool) -> dict:
    print(f'>>> 运行 [{label}] @ {base_url}', flush=True)
    c = _client(base_url, username=username, password=password, insecure=insecure)
    rows: list[dict] = []
    for brief in BRIEFS:
        out, err = _generate(c, club, brief['prompt'])
        if out is None:
            rows.append({'id': brief['id'], 'error': err})
            print(f'    {brief["id"]}: ERROR {err}', flush=True)
            continue
        a = analyze(out, brief['expect'])
        a['id'] = brief['id']
        rows.append(a)
        print(f'    {brief["id"]}: title={a["title"]!r} blocks={a["blocks"]} '
              f'facts={len(a["fact_hits"])}/{a["fact_total"]}', flush=True)
    c.close()

    ok = [r for r in rows if 'error' not in r]
    titles = [r['title'] for r in ok]
    blobs = [r['blob'] for r in ok]
    agg = {
        'label': label,
        'url': base_url,
        'total': len(rows),
        'ok': len(ok),
        'errors': [r for r in rows if 'error' in r],
        'distinct_titles': len(set(titles)),
        'default_titles': sum(1 for r in ok if r['default_title']),
        'mean_similarity': round(_pairwise_mean(blobs), 4),
        'mean_blocks': round(sum(r['blocks'] for r in ok) / len(ok), 2) if ok else 0,
        'distinct_block_signatures': len({tuple(r['types']) for r in ok}),
        'mean_chars': int(sum(r['chars'] for r in ok) / len(ok)) if ok else 0,
        'placeholder_hits': sum(1 for r in ok if r['placeholder']),
        'incomplete': sum(1 for r in ok if r['missing_required']),
        'fact_rate': round(sum(len(r['fact_hits']) for r in ok) / sum(r['fact_total'] for r in ok), 4) if ok else 0,
        'rows': rows,
    }
    return agg


def render(mock: dict, live: dict | None) -> str:
    L: list[str] = []
    L.append('# ClubOS 活动生成：mock 与真实大模型质量对照')
    L.append('')
    L.append('> 同一批活动原始资料，分别打到 mock 模式实例与 live 模式实例。所有指标由响应 JSON 客观计算。')
    L.append('')
    L.append('## 一、总体指标')
    L.append('')
    cols = ['指标', f'mock（{mock["url"]}）']
    if live:
        cols.append(f'真实模型（{live["url"]}）')
    L.append('| ' + ' | '.join(cols) + ' |')
    L.append('|' + '---|' * len(cols))

    def row(name: str, fn) -> None:
        vals = [name, str(fn(mock))]
        if live:
            vals.append(str(fn(live)))
        L.append('| ' + ' | '.join(vals) + ' |')

    row('成功生成 / 总数', lambda a: f'{a["ok"]}/{a["total"]}')
    row('**不同标题数**（越少越雷同）', lambda a: f'{a["distinct_titles"]}/{a["ok"]}')
    row('**落到兜底标题"周末自然计划"的次数**', lambda a: a['default_titles'])
    row('**两两文本平均相似度**（越高越模板化）', lambda a: a['mean_similarity'])
    row('不同 block 结构签名数', lambda a: f'{a["distinct_block_signatures"]}/{a["ok"]}')
    row('平均 block 数', lambda a: a['mean_blocks'])
    row('平均产出字符数', lambda a: a['mean_chars'])
    row('命中 mock 占位话术的次数', lambda a: a['placeholder_hits'])
    row('必需字段缺失次数', lambda a: a['incomplete'])
    row('**事实保真率**（简报事实出现比例）', lambda a: f'{a["fact_rate"]:.2%}')
    L.append('')

    L.append('## 二、逐条明细')
    L.append('')
    for i, brief in enumerate(BRIEFS):
        L.append(f'### {brief["id"]}')
        L.append('')
        L.append(f'简报：`{brief["prompt"]}`')
        L.append('')
        hdr = '| 来源 | 标题 | block 数 | block 类型 | 事实命中 | 占位话术 |'
        L.append(hdr)
        L.append('|---|---|---|---|---|---|')
        for arm in [mock] + ([live] if live else []):
            r = next((x for x in arm['rows'] if x.get('id') == brief['id']), None)
            if r is None:
                continue
            if 'error' in r:
                L.append(f'| {arm["label"]} | — | — | — | — | ❌ {r["error"][:60]} |')
                continue
            L.append(f'| {arm["label"]} | {r["title"]} | {r["blocks"]} | '
                     f'{",".join(r["types"])} | {len(r["fact_hits"])}/{r["fact_total"]} | '
                     f'{",".join(r["placeholder"]) or "—"} |')
        L.append('')

    errs = [e for e in (mock['errors'] + (live['errors'] if live else []))]
    if errs:
        L.append('## 三、失败项')
        L.append('')
        for e in errs:
            L.append(f'- `{e["id"]}`: {e["error"]}')
        L.append('')
    return '\n'.join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mock-url', default='http://127.0.0.1:8000')
    ap.add_argument('--live-url', default='')
    ap.add_argument('--club', type=int, default=1)
    ap.add_argument('--username', default='')
    ap.add_argument('--password', default='')
    ap.add_argument('--insecure', action='store_true', help='HTTPS 自签证书时跳过校验')
    ap.add_argument('--db', default='', help='读取 ai_usage_records 的 sqlite 路径（可选）')
    ap.add_argument('--out', default='', help='写出 markdown 报告路径（可选）')
    args = ap.parse_args()

    mock = run_arm('mock', args.mock_url, args.club, username=args.username,
                   password=args.password, insecure=args.insecure)
    live = None
    if args.live_url:
        live = run_arm('真实模型', args.live_url, args.club, username=args.username,
                       password=args.password, insecure=args.insecure)

    md = render(mock, live)
    print()
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding='utf-8')
        print(f'\n[报告已写入] {args.out}')

    for arm, provs in ((mock, ['mock']), (live, ['deepseek', 'qwen'])):
        if not arm:
            continue
        u = _usage(args.db, provs)
        if u:
            print(f'\n[{arm["label"]}] ai_usage_records 最近 {len(u)} 条:')
            for r in u[:8]:
                print('   ', json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
