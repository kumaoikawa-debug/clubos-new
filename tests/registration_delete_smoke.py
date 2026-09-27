"""删除活动的报名护栏：只有「未取消且未退款」的报名才拦得住。

背景：护栏原判据是 `status != 'cancelled'`，于是「取消/退款成功的报名」（refunded）
依然算 live —— 表现为「活动刚处理完全部退款就永远删不掉」，测试数据清理因此被卡死
（先逐笔 cancel 让报名变 refunded，再删，依然 409）。

本测试锁住不变量：
1. 有 paid 报名 ⇒ 仍然 409 拒绝（护栏本身没被拆坏）；
2. 报名为 refunded / cancelled ⇒ 允许删除，且报名与团期行一并删除，不留孤儿；
3. 无报名 ⇒ 允许删除。

自带临时库隔离，不需要外部 CLUBOS_DB_PATH（app.py 在模块级就会 init_db，
不隔离会落到项目真库）。不打大模型、不起服务。
"""
import os
import sqlite3
import sys
import tempfile

_d = tempfile.mkdtemp(prefix="clubos_regdel_")
os.environ["CLUBOS_DB_PATH"] = os.path.join(_d, "clubos.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "__pycache__"), exist_ok=True)

from fastapi import HTTPException

import db
import app

db.init_db()

FAILED = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))
    if not cond:
        FAILED.append(label)


con = sqlite3.connect(os.environ["CLUBOS_DB_PATH"])
CLUB = 1


def mkact():
    cur = con.execute(
        "INSERT INTO activities(club_id,title,status,activity_master_json,detail_json) VALUES(?,?,?,?,?)",
        (CLUB, "护栏用例", "draft", "{}", "{}"))
    aid = cur.lastrowid
    o = con.execute(
        "INSERT INTO activity_occurrences(activity_id,club_id,start_at) VALUES(?,?,?)",
        (aid, CLUB, "2026-12-01 09:00:00"))
    con.commit()
    return aid, o.lastrowid


def mkreg(aid, occ, status):
    con.execute(
        "INSERT INTO registrations(activity_id,occurrence_id,club_id,user_id,status,original_amount,amount)"
        " VALUES(?,?,?,?,?,?,?)", (aid, occ, CLUB, 1, status, 198.0, 198.0))
    con.commit()


def expect_ok(aid, label):
    try:
        r = app.delete_activity(CLUB, aid)
        ck(label, r["deleted"] == aid, r)
        return True
    except HTTPException as e:
        ck(label, False, "status=%s %s" % (e.status_code, e.detail))
        return False


def expect_409(aid, label):
    try:
        app.delete_activity(CLUB, aid)
        ck(label, False, "竟然删成功了（护栏失效）")
    except HTTPException as e:
        ck(label, e.status_code == 409 and "未取消的报名" in str(e.detail),
           "status=%s %s" % (e.status_code, e.detail))


# 1. 无报名 ⇒ 可删
aid, _ = mkact()
expect_ok(aid, "无报名可删")
ck("活动行已消失", not con.execute("SELECT 1 FROM activities WHERE id=?", (aid,)).fetchone())

# 2. paid ⇒ 仍然拦（护栏本体没被拆坏）
aid, occ = mkact()
mkreg(aid, occ, "paid")
expect_409(aid, "paid 报名仍然拦截删除")

# 3. refunded（已全额退款）⇒ 放行（本次修复）
aid, occ = mkact()
mkreg(aid, occ, "refunded")
expect_ok(aid, "refunded 报名不再拦截删除")

# 4. cancelled ⇒ 本来就放行
aid, occ = mkact()
mkreg(aid, occ, "cancelled")
expect_ok(aid, "cancelled 报名本就放行")

# 5. 级联：报名与团期一并删除，不留孤儿
aid, occ = mkact()
mkreg(aid, occ, "cancelled")
expect_ok(aid, "级联删除用例可删")
ck("报名行一并删除", not con.execute("SELECT 1 FROM registrations WHERE activity_id=?", (aid,)).fetchone())
ck("团期行一并删除", not con.execute("SELECT 1 FROM activity_occurrences WHERE activity_id=?", (aid,)).fetchone())

print()
if FAILED:
    print("RESULT: %d FAILED" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("RESULT: ALL PASS")
