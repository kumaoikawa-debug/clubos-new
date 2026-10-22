#!/usr/bin/env python3
"""把本地缺失的 extract 图片从线上未鉴权直链拉回到本地 static/uploads。

用法：python scripts/fetch_missing_uploads.py [club_id] [activity_ids...]
只读地从 /tmp 库或项目库里取出 activities.media 的 url 列表，逐个 HEAD/GET 对比本地是否存在。
"""
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request

HOST = 'https://clubos-backend.app.workbuddy.host'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, 'static')


def local_path(url: str) -> str:
    rel = url.split('/static/', 1)[1] if '/static/' in url else url.lstrip('/')
    return os.path.join(STATIC, urllib.parse.unquote(rel))


def remote_url(url: str) -> str:
    rel = url.split('/static/', 1)[1] if '/static/' in url else url.lstrip('/')
    return HOST + '/static/' + '/'.join(urllib.parse.quote(p) for p in rel.split('/'))


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'clubos.db')
    club = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    ids = [int(x) for x in sys.argv[3:]] or None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT id, activity_master_json FROM activities WHERE club_id=? ORDER BY id', (club,)).fetchall()
    wanted, total, ok, skip, bad = [], 0, 0, 0, []
    for r in rows:
        if ids and int(r['id']) not in ids:
            continue
        try:
            m = json.loads(r['activity_master_json'] or '{}')
        except Exception:
            m = {}
        for it in (m.get('media') or []):
            u = str(it.get('url') or '')
            if '/uploads/' not in u:
                continue
            total += 1
            lp = local_path(u)
            if os.path.exists(lp) and os.path.getsize(lp) > 0:
                skip += 1
                continue
            wanted.append((int(r['id']), u, lp))
    print(f'media refs={total} local_ok={skip} missing={len(wanted)}')
    for aid, u, lp in wanted:
        try:
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            req = urllib.request.Request(remote_url(u), headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=90) as resp, open(lp, 'wb') as f:
                f.write(resp.read())
            sz = os.path.getsize(lp)
            print(f'  act{aid} {os.path.basename(lp)[:46]} {sz//1024}KB')
            ok += 1
        except Exception as e:
            bad.append((u, str(e)[:80]))
            print(f'  FAIL act{aid} {os.path.basename(lp)[:46]} -> {e}')
    print(f'== downloaded={ok} failed={len(bad)}')
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())
