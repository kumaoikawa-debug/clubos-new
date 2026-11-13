"""P3 结算编排：把 P1/P2 的三类「只读权益」真正接进成交链路。

设计纪律（与前几轮一致）
------------------------
1. **显式 opt-in**：只有 checkout 请求里带 `settlement` 块且至少声明一项能力才启用。
   不带参数的既有下单链路一个字节都不改、一张表都不写。
2. **金额整数分**：订单应付、礼品卡抵扣、佣金全部用整数分，`cash_amount`（元）由 app 层换算。
3. **建单即锁定**：礼品卡在 create intent 时真实扣减余额（CONSUME 流水，source=CHECKOUT:<checkout_id>），
   避免「现金少收了、余额却在支付期间被花掉」的资损。支付取消/失败由 app 层写 REFUND 冲回。
4. **confirm 统一编排**：8 条支付成功路径统一走 `_confirm_checkout`，佣金只在该单有编排记录时入账；
   `accrue` 本身按 (club, orderType, orderId, affiliate) 幂等，重复 confirm 不会重复返佣。
5. **状态机 + 事件审计**：RESERVED → SETTLED / RELEASED / FAILED；FAILED 可重试回 SETTLED。
   已 SETTLED 的单不允许 release（成交后不能静默冲回）。
6. **零依赖**：stdlib-only、cursor-only（调用方管事务），不 import 其它 domain 模块，
   以便 tests 用 importlib 单文件加载。礼品卡 / 分销的真实写入由 app 层注入调用。
7. 所有异常内部吞掉并返回 reason，绝不外抛阻断支付主流程。

口径说明
--------
- 拼团价：建单前用成团价替换 occurrence.price（内存副本，不落库），积分/券/会员折扣
  全部基于新单价重算，是真正的 1:1 口径；本模块只保存对账快照，不二次扣减。
- 佣金基数：`order_amount_cents` = 礼品卡抵扣前的订单应付总额。礼品卡是俱乐部已收的
  预付资金，不是平台补贴，所以不应从佣金基数里扣掉。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

# ── 常量 ────────────────────────────────────────────────────────────────────
STATUSES = ('RESERVED', 'SETTLED', 'RELEASED', 'FAILED')
SETTLEABLE = ('RESERVED', 'FAILED')
EVENT_TYPES = ('RESERVE', 'SETTLE', 'RELEASE', 'RETRY', 'FAIL')
MAX_AMOUNT_CENTS = 100_000_000          # 单笔上限 ¥1,000,000
MAX_RATIO = 100
TS_MAX = 10 ** 12
TZ = timezone(timedelta(hours=8))

_DDL = """
CREATE TABLE IF NOT EXISTS checkout_settlements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  checkout_id TEXT NOT NULL UNIQUE,
  club_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'activity',
  status TEXT NOT NULL DEFAULT 'RESERVED',
  order_amount_cents INTEGER NOT NULL DEFAULT 0,
  cash_before_cents INTEGER NOT NULL DEFAULT 0,
  group_buy_cents INTEGER NOT NULL DEFAULT 0,
  gift_card_cents INTEGER NOT NULL DEFAULT 0,
  cash_after_cents INTEGER NOT NULL DEFAULT 0,
  group_buy_json TEXT,
  commission_enabled INTEGER NOT NULL DEFAULT 0,
  commission_cents INTEGER NOT NULL DEFAULT 0,
  commission_reason TEXT,
  registration_id INTEGER,
  request_json TEXT,
  detail_json TEXT,
  operator TEXT,
  created_at TEXT,
  updated_at TEXT,
  settled_at TEXT
);
CREATE TABLE IF NOT EXISTS checkout_settlement_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  checkout_id TEXT NOT NULL,
  club_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  detail_json TEXT,
  operator TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_css_club ON checkout_settlements(club_id, id);
CREATE INDEX IF NOT EXISTS idx_css_status ON checkout_settlements(status, id);
CREATE INDEX IF NOT EXISTS idx_cse_checkout ON checkout_settlement_events(checkout_id, id);
CREATE INDEX IF NOT EXISTS idx_cse_club ON checkout_settlement_events(club_id, id);
"""


def ensure_tables(c):
    """幂等建表。生产由 db.py 的 `_migrate_v045_checkout_settlement` 负责；
    这里只给单测 / 隔离库提供同一份 DDL，避免两处结构漂移。"""
    c.executescript(_DDL)


# ── 基础工具 ────────────────────────────────────────────────────────────────
def _int(v, default=0, lo=0, hi=MAX_AMOUNT_CENTS):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _ts(now=None):
    if now is None:
        return int(time.time())
    return max(0, min(TS_MAX, int(now)))


def _ts_to_str(ts):
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec='seconds')


def to_cents(v) -> int:
    """元 → 分。字符串/None/NaN/负数一律安全回落 0（脏输入绝不抛异常）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f or f in (float('inf'), float('-inf')):
        return 0
    return max(0, min(MAX_AMOUNT_CENTS, int(round(f * 100))))


def from_cents(v) -> float:
    return round(_int(v, 0, 0, MAX_AMOUNT_CENTS) / 100, 2)


def _jload(s, fallback=None):
    if fallback is None:
        fallback = {}
    try:
        v = json.loads(s) if s else None
        return v if isinstance(v, (dict, list)) else fallback
    except Exception:
        return fallback


def _jdump(v):
    try:
        return json.dumps(v or {}, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        return '{}'


# ── 请求归一（opt-in 总闸门）─────────────────────────────────────────────────
def normalize_request(payload) -> dict | None:
    """解析 checkout payload 的 `settlement` 块；返回 None = 完全不启用编排。

    - 不存在 / 非 dict / 空块 / 没有任何明确开关 → None（既有链路零影响）
    - 只接受白名单键，脏值一律夹逼或丢弃，绝不抛异常
    - `commission` 默认 False：不显式声明就不自动返佣，避免改变既有成交口径
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get('settlement')
    if not isinstance(raw, dict) or not raw:
        return None
    use_gift = bool(raw.get('useGiftCard'))
    team_id = _int(raw.get('teamId'), 0, 0, 2 ** 31 - 1)
    commission = bool(raw.get('commission'))
    if not (use_gift or team_id or commission):
        return None
    requested = 0
    if use_gift:
        requested = _int(raw.get('giftCardCents'), 0, 0, MAX_AMOUNT_CENTS)
    return {
        'useGiftCard': use_gift,
        'giftCardCents': requested,       # 0 = 自动按「余额 / 应付 / 比例」取最大可用
        'teamId': team_id,
        'commission': commission,
    }


def gift_card_quota(*, payable_cents: int, balance_cents: int,
                    max_ratio: int = 100, requested_cents: int = 0) -> dict:
    """本单礼品卡可抵扣额（纯函数，不读库不写库）。

    三重封顶：余额、应付、比例；`requested_cents>0` 时再叠一层「用户指定额」。
    """
    payable = _int(payable_cents, 0, 0, MAX_AMOUNT_CENTS)
    balance = _int(balance_cents, 0, 0, MAX_AMOUNT_CENTS)
    ratio = _int(max_ratio, 100, 0, MAX_RATIO)
    ceiling = min(balance, payable, int(payable * ratio / 100))
    if requested_cents:
        ceiling = min(ceiling, _int(requested_cents, 0, 0, MAX_AMOUNT_CENTS))
    usable = max(0, ceiling)
    return {
        'payableCents': payable,
        'balanceCents': balance,
        'usableCents': usable,
        'usableAmount': from_cents(usable),
        'remainingCents': max(0, payable - usable),
        'remainingAmount': from_cents(max(0, payable - usable)),
        'maxPayRatio': ratio,
    }


def group_buy_snapshot(quote, participant_count: int = 1) -> dict | None:
    """成团价对账快照（纯函数）。只有 SUCCESS 且价差为正才返回。

    拼团价本身在 app 层通过替换 occurrence.price 生效（积分/券/会员折扣同源重算），
    这里不二次扣钱，只留可审计的「原价 / 成团价 / 省了多少」。
    """
    if not isinstance(quote, dict):
        return None
    if str(quote.get('status') or '').upper() != 'SUCCESS':
        return None
    pc = _int(quote.get('priceCents'), 0, 0, MAX_AMOUNT_CENTS)
    opc = _int(quote.get('originalPriceCents'), 0, 0, MAX_AMOUNT_CENTS)
    if pc <= 0 or opc <= pc:
        return None
    n = _int(participant_count, 1, 1, 10000)
    save = (opc - pc) * n
    return {
        'teamId': _int(quote.get('teamId'), 0, 0, 2 ** 31 - 1),
        'campaignId': _int(quote.get('campaignId'), 0, 0, 2 ** 31 - 1),
        'originalPriceCents': opc,
        'priceCents': pc,
        'participantCount': n,
        'savingCents': save,
        'savingAmount': from_cents(save),
        'appliedToPayable': True,
    }


def build_plan(*, cash_cents: int, req: dict | None, balance_cents: int = 0,
               max_ratio: int = 100, group_quote=None,
               participant_count: int = 1) -> dict:
    """组合出建单需要的全部金额（纯函数）。cash_cents 是积分/券/会员折扣后的现金应付。

    扣减顺序（与 price-quote 的 opt-in 预览严格同源，保证「报价=实收」）：
      cash_cents → 拼团成团立减（封顶 cash）→ 礼品卡抵扣（封顶剩余应付/余额/比例）→ 实收现金

    - `orderAmountCents` = 拼团优惠后的商品成交额，作分销佣金基数：
      拼团优惠不是俱乐部收入，礼品卡是俱乐部已收的预付资金，所以只扣前者、不扣后者。
    - 拼团差额按「(原价−成团价)×人数」计算，与 group_buy.quote_for_team 的口径一致。
    """
    r = req if isinstance(req, dict) else {}
    cash = _int(cash_cents, 0, 0, MAX_AMOUNT_CENTS)
    gb = group_buy_snapshot(group_quote, participant_count)
    group_cents = min(int(gb['savingCents']) if gb else 0, cash)
    after_group = max(0, cash - group_cents)
    bal = _int(balance_cents, 0, 0, MAX_AMOUNT_CENTS)
    quota = gift_card_quota(payable_cents=after_group, balance_cents=bal,
                            max_ratio=max_ratio,
                            requested_cents=_int(r.get('giftCardCents'), 0, 0, MAX_AMOUNT_CENTS)) \
        if r.get('useGiftCard') else None
    used = int(quota['usableCents']) if quota else 0
    return {
        'enabled': bool(r),
        'cashBeforeCents': cash,
        'groupBuyCents': group_cents,
        'orderAmountCents': after_group,
        'giftCardCents': used,
        'cashAfterCents': max(0, after_group - used),
        'giftCard': quota,
        'groupBuy': gb,
        'commissionEnabled': bool(r.get('commission')),
    }


# ── cursor-only DAO（调用方负责事务）─────────────────────────────────────────
def _row_dict(r):
    return dict(r) if r is not None else None


def add_event(c, *, checkout_id: str, club_id: int, event_type: str,
              from_status=None, to_status=None, detail=None,
              operator=None, now=None) -> dict:
    """写一条编排事件。异常吞掉，绝不外抛。"""
    try:
        et = str(event_type or '').upper()
        if et not in EVENT_TYPES:
            return {'ok': False, 'reason': 'bad-event'}
        cur = c.execute(
            """INSERT INTO checkout_settlement_events
               (checkout_id,club_id,event_type,from_status,to_status,detail_json,operator,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (str(checkout_id)[:80], int(club_id), et,
             str(from_status or '')[:24] or None, str(to_status or '')[:24] or None,
             _jdump(detail), str(operator or '')[:60] or None, _ts_to_str(_ts(now))))
        return {'ok': True, 'eventId': cur.lastrowid}
    except Exception as e:
        return {'ok': False, 'reason': 'exception', 'error': str(e)[:120]}


def create_record(c, *, checkout_id: str, club_id: int, user_id: int,
                  plan: dict, request: dict | None = None, kind: str = 'activity',
                  operator=None, now=None) -> dict:
    """建编排记录（状态 RESERVED）。同一 checkout_id 幂等：重复返回 duplicated。

    注意：这里**不动礼品卡余额**；app 层必须先成功锁定余额再调用本函数，
    以保证「表里有记录 ⇒ 钱已锁定」。
    """
    res = {'ok': False, 'reason': '', 'recordId': None}
    try:
        cid = str(checkout_id or '').strip()[:80]
        if not cid:
            res['reason'] = 'invalid-checkout'
            return res
        p = plan if isinstance(plan, dict) else {}
        exists = c.execute('SELECT id,status FROM checkout_settlements WHERE checkout_id=?',
                           (cid,)).fetchone()
        if exists:
            res['reason'] = 'duplicated'
            res['recordId'] = int(exists['id'])
            res['status'] = exists['status']
            return res
        created = _ts_to_str(_ts(now))
        cur = c.execute(
            """INSERT INTO checkout_settlements
               (checkout_id,club_id,user_id,kind,status,
                order_amount_cents,cash_before_cents,group_buy_cents,gift_card_cents,cash_after_cents,
                group_buy_json,commission_enabled,commission_cents,commission_reason,
                registration_id,request_json,detail_json,operator,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, int(club_id), int(user_id), str(kind or 'activity')[:24], 'RESERVED',
             _int(p.get('orderAmountCents'), 0, 0, MAX_AMOUNT_CENTS),
             _int(p.get('cashBeforeCents'), 0, 0, MAX_AMOUNT_CENTS),
             _int(p.get('groupBuyCents'), 0, 0, MAX_AMOUNT_CENTS),
             _int(p.get('giftCardCents'), 0, 0, MAX_AMOUNT_CENTS),
             _int(p.get('cashAfterCents'), 0, 0, MAX_AMOUNT_CENTS),
             _jdump(p.get('groupBuy')) if p.get('groupBuy') else None,
             1 if p.get('commissionEnabled') else 0,
             0, None, None, _jdump(request),
             _jdump({'giftCard': p.get('giftCard') or {}}),
             str(operator or '')[:60] or None, created, created))
        res.update(ok=True, reason='ok', recordId=cur.lastrowid, status='RESERVED')
        add_event(c, checkout_id=cid, club_id=int(club_id), event_type='RESERVE',
                  from_status=None, to_status='RESERVED',
                  detail={'giftCardCents': p.get('giftCardCents', 0),
                          'cashAfterCents': p.get('cashAfterCents', 0)},
                  operator=operator, now=now)
        return res
    except Exception as e:
        res['reason'] = 'exception'
        res['error'] = str(e)[:120]
        return res


def get_record(c, checkout_id: str) -> dict | None:
    try:
        return _row_dict(c.execute(
            'SELECT * FROM checkout_settlements WHERE checkout_id=?',
            (str(checkout_id),)).fetchone())
    except Exception:
        return None


def settle_record(c, checkout_id: str, *, registration_id=None,
                  commission_cents: int = 0, commission_reason: str = '',
                  detail=None, operator=None, now=None) -> dict:
    """支付成功：RESERVED/FAILED → SETTLED。SETTLED 重入幂等返回已有结果。"""
    res = {'ok': False, 'reason': '', 'status': None, 'idempotent': False}
    try:
        r = get_record(c, checkout_id)
        if not r:
            res['reason'] = 'no-record'
            return res
        st = str(r['status'] or '')
        if st == 'SETTLED':
            res.update(ok=True, reason='already-settled', status='SETTLED', idempotent=True,
                       commissionCents=int(r['commission_cents'] or 0))
            return res
        if st not in SETTLEABLE:
            res.update(reason='bad-status', status=st)
            return res
        created = _ts_to_str(_ts(now))
        c.execute(
            """UPDATE checkout_settlements
                  SET status='SETTLED', registration_id=COALESCE(?,registration_id),
                      commission_cents=?, commission_reason=?,
                      detail_json=?, updated_at=?, settled_at=?
                WHERE id=?""",
            (int(registration_id) if registration_id else None,
             _int(commission_cents, 0, 0, MAX_AMOUNT_CENTS),
             str(commission_reason or '')[:120] or None,
             _jdump(detail), created, created, int(r['id'])))
        add_event(c, checkout_id=str(checkout_id), club_id=int(r['club_id']),
                  event_type='RETRY' if st == 'FAILED' else 'SETTLE',
                  from_status=st, to_status='SETTLED',
                  detail={'registrationId': registration_id,
                          'commissionCents': commission_cents,
                          'commissionReason': commission_reason},
                  operator=operator, now=now)
        res.update(ok=True, reason='ok', status='SETTLED')
        return res
    except Exception as e:
        res['reason'] = 'exception'
        res['error'] = str(e)[:120]
        return res


def fail_record(c, checkout_id: str, *, reason: str = '', detail=None,
                operator=None, now=None) -> dict:
    """结算编排失败（佣金入账异常等）：只标记，绝不抛异常阻断支付。"""
    res = {'ok': False, 'reason': ''}
    try:
        r = get_record(c, checkout_id)
        if not r:
            res['reason'] = 'no-record'
            return res
        st = str(r['status'] or '')
        if st not in SETTLEABLE:
            res.update(reason='bad-status', status=st)
            return res
        created = _ts_to_str(_ts(now))
        c.execute(
            """UPDATE checkout_settlements SET status='FAILED', commission_reason=?,
                      detail_json=?, updated_at=? WHERE id=?""",
            (str(reason or '')[:120] or None, _jdump(detail), created, int(r['id'])))
        add_event(c, checkout_id=str(checkout_id), club_id=int(r['club_id']),
                  event_type='FAIL', from_status=st, to_status='FAILED',
                  detail={'reason': reason}, operator=operator, now=now)
        res.update(ok=True, reason='ok', status='FAILED')
        return res
    except Exception as e:
        res['reason'] = 'exception'
        res['error'] = str(e)[:120]
        return res


def release_record(c, checkout_id: str, *, reason: str = '', detail=None,
                   operator=None, now=None) -> dict:
    """支付取消 / 未支付过期：RESERVED/FAILED → RELEASED（由 app 层同时冲回礼品卡余额）。

    已 SETTLED 的成交单一律拒绝，避免「订单已成、余额被静默退回」的资损。
    """
    res = {'ok': False, 'reason': '', 'status': None, 'giftCardCents': 0}
    try:
        r = get_record(c, checkout_id)
        if not r:
            res['reason'] = 'no-record'
            return res
        st = str(r['status'] or '')
        if st == 'RELEASED':
            res.update(ok=True, reason='already-released', status='RELEASED', idempotent=True,
                       giftCardCents=int(r['gift_card_cents'] or 0))
            return res
        if st not in SETTLEABLE:
            res.update(reason='bad-status', status=st)
            return res
        created = _ts_to_str(_ts(now))
        c.execute(
            """UPDATE checkout_settlements SET status='RELEASED', detail_json=?,
                      updated_at=? WHERE id=?""",
            (_jdump(detail or {'releaseReason': reason}), created, int(r['id'])))
        add_event(c, checkout_id=str(checkout_id), club_id=int(r['club_id']),
                  event_type='RELEASE', from_status=st, to_status='RELEASED',
                  detail={'reason': reason, 'giftCardCents': int(r['gift_card_cents'] or 0)},
                  operator=operator, now=now)
        res.update(ok=True, reason='ok', status='RELEASED',
                   giftCardCents=int(r['gift_card_cents'] or 0))
        return res
    except Exception as e:
        res['reason'] = 'exception'
        res['error'] = str(e)[:120]
        return res


def view(r) -> dict | None:
    """后台/API 视图：驼峰键 + 元金额，不暴露内部 JSON 原文。"""
    if not r:
        return None
    d = dict(r)
    return {
        'id': d.get('id'),
        'checkoutId': d.get('checkout_id'),
        'clubId': d.get('club_id'),
        'userId': d.get('user_id'),
        'kind': d.get('kind'),
        'status': d.get('status'),
        'orderAmountCents': int(d.get('order_amount_cents') or 0),
        'orderAmount': from_cents(d.get('order_amount_cents')),
        'cashBeforeCents': int(d.get('cash_before_cents') or 0),
        'cashBefore': from_cents(d.get('cash_before_cents')),
        'groupBuyCents': int(d.get('group_buy_cents') or 0),
        'groupBuyAmount': from_cents(d.get('group_buy_cents')),
        'giftCardCents': int(d.get('gift_card_cents') or 0),
        'giftCardAmount': from_cents(d.get('gift_card_cents')),
        'cashAfterCents': int(d.get('cash_after_cents') or 0),
        'cashAfter': from_cents(d.get('cash_after_cents')),
        'groupBuy': _jload(d.get('group_buy_json')),
        'commissionEnabled': bool(d.get('commission_enabled')),
        'commissionCents': int(d.get('commission_cents') or 0),
        'commissionAmount': from_cents(d.get('commission_cents')),
        'commissionReason': d.get('commission_reason'),
        'registrationId': d.get('registration_id'),
        'request': _jload(d.get('request_json')),
        'operator': d.get('operator'),
        'createdAt': d.get('created_at'),
        'updatedAt': d.get('updated_at'),
        'settledAt': d.get('settled_at'),
    }


def list_records(c, *, club_id=None, status=None, limit: int = 50,
                 offset: int = 0) -> list:
    try:
        where, args = [], []
        if club_id is not None:
            where.append('club_id=?')
            args.append(int(club_id))
        if status:
            where.append('status=?')
            args.append(str(status).upper())
        sql = 'SELECT * FROM checkout_settlements'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY id DESC LIMIT ? OFFSET ?'
        args += [_int(limit, 50, 1, 200), _int(offset, 0, 0, 10 ** 9)]
        return [_row_dict(x) for x in c.execute(sql, args).fetchall()]
    except Exception:
        return []


def list_events(c, checkout_id: str, limit: int = 50) -> list:
    try:
        rs = c.execute(
            """SELECT * FROM checkout_settlement_events
                WHERE checkout_id=? ORDER BY id DESC LIMIT ?""",
            (str(checkout_id), _int(limit, 50, 1, 200))).fetchall()
        return [{
            'id': x['id'], 'eventType': x['event_type'],
            'fromStatus': x['from_status'], 'toStatus': x['to_status'],
            'detail': _jload(x['detail_json']), 'operator': x['operator'],
            'createdAt': x['created_at'],
        } for x in rs]
    except Exception:
        return []


def summary(c, *, club_id=None) -> dict:
    """后台汇总：各状态单数、礼品卡抵扣总额、佣金总额、净现金。"""
    out = {'total': 0, 'byStatus': {}, 'groupBuyCents': 0,
           'groupBuyAmount': 0.0, 'giftCardCents': 0,
           'giftCardAmount': 0.0, 'commissionCents': 0,
           'commissionAmount': 0.0, 'cashAfterCents': 0, 'cashAfterAmount': 0.0}
    try:
        where, args = ('WHERE club_id=?', [int(club_id)]) if club_id is not None else ('', [])
        r = c.execute(
            f"""SELECT COUNT(*) n,
                       COALESCE(SUM(group_buy_cents),0) gb,
                       COALESCE(SUM(gift_card_cents),0) gc,
                       COALESCE(SUM(commission_cents),0) cm,
                       COALESCE(SUM(cash_after_cents),0) cash
                  FROM checkout_settlements {where}""", args).fetchone()
        out['total'] = int(r['n'] or 0)
        out['groupBuyCents'] = int(r['gb'] or 0)
        out['giftCardCents'] = int(r['gc'] or 0)
        out['commissionCents'] = int(r['cm'] or 0)
        out['cashAfterCents'] = int(r['cash'] or 0)
        for row in c.execute(
                f'SELECT status, COUNT(*) n FROM checkout_settlements {where} GROUP BY status',
                args).fetchall():
            out['byStatus'][str(row['status'])] = int(row['n'] or 0)
    except Exception:
        pass
    out['groupBuyAmount'] = from_cents(out['groupBuyCents'])
    out['giftCardAmount'] = from_cents(out['giftCardCents'])
    out['commissionAmount'] = from_cents(out['commissionCents'])
    out['cashAfterAmount'] = from_cents(out['cashAfterCents'])
    return out
