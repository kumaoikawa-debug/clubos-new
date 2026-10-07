from __future__ import annotations
from fastapi import HTTPException
from db import conn, row, setting

AI_COST_KEYS = {
    'detail': 'ai_cost_detail',
    'wechat': 'ai_cost_wechat',
    'xhs': 'ai_cost_xhs',
    'poster': 'ai_cost_poster',
    'recap': 'ai_cost_recap',
    # 「AI 宣传长图」（2026-10-07 新增）：模型直出成品 HTML，与公众号图文同量级，
    # 但要多跑一次图片描述（视觉模型），所以按公众号的价格计，不新增平台配置项。
    'longpic': 'ai_cost_wechat',
}


def credit_cost(task_type: str) -> int:
    key = AI_COST_KEYS.get(task_type)
    if not key:
        raise HTTPException(400, f'未知 AI 任务类型: {task_type}')
    return int(setting(key, 10))


def _active_account(club_id: int):
    with conn() as c:
        club = row(c.execute('SELECT id,status FROM clubs WHERE id=?', (club_id,)))
        acc = row(c.execute('SELECT * FROM ai_credit_accounts WHERE club_id=?', (club_id,)))
    if not club:
        raise HTTPException(404, '俱乐部不存在')
    if club.get('status') != 'active':
        raise HTTPException(403, '俱乐部当前未启用，AI 调用已暂停')
    return acc


def ensure_credits(club_id: int, task_type: str) -> int:
    """只校验额度，不参与模型选择，也不改变模型能力。"""
    cost = credit_cost(task_type)
    acc = _active_account(club_id)
    if not acc or int(acc['balance']) < cost:
        raise HTTPException(402, 'AI Credits不足')
    return cost


def charge_credits(club_id: int, task_type: str, usage_id: int | None = None) -> int:
    """生成成功后扣费。余额永远不会决定使用哪个模型。"""
    cost = credit_cost(task_type)
    # Re-check status immediately before charging so a disabled club cannot continue a stale request.
    _active_account(club_id)
    with conn() as c:
        cur = c.execute(
            'UPDATE ai_credit_accounts SET balance=balance-? WHERE club_id=? AND balance>=?',
            (cost, club_id, cost),
        )
        if cur.rowcount != 1:
            raise HTTPException(402, 'AI Credits不足')
        c.execute(
            '''INSERT INTO ai_credit_ledger(club_id,type,amount,source_type,source_id,note)
               VALUES(?,?,?,?,?,?)''',
            (club_id, 'consume', -cost, 'ai_usage', str(usage_id or ''), f'{task_type} AI生成'),
        )
        if usage_id:
            c.execute('UPDATE ai_usage_records SET credits_charged=? WHERE id=?', (cost, usage_id))
    return cost
