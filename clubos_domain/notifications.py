"""通知通道抽象 + 模板（v0.36，P0-4；v0.40 接入设置中枢策略）。

参考「远拓旅游 SaaS · 订单提醒 / 多渠道通知」：把"通知"从单点（仅企微 Webhook）升级为
**通道抽象 + 模板**两层。渠道：wechat(企微机器人, 复用 club_integrations) / email / sms / inapp(站内信)。
模板按 code 区分业务场景（报名成功 / 订单支付 / 券到期 / 活动前提醒 / 库存预警…），可带 {{变量}}。

设计要点（沿用既有三铁律哲学）：
1. 通道未启用 / 模板未配置 / 变量缺失 → 静默跳过，绝不因通知失败阻断主流程；
2. 配置（webhook / smtp / 网关密钥）只存 config_json，发送失败不影响下单；
3. inapp 站内信为默认可用通道（无需外部网关），wechat 复用现有企微集成，email/sms 配置齐备才真发。

v0.40：dispatch 新增**可选** hub 参数（由 settings_hub.notify_policy 提供），用于
「外发总开关」与「免打扰时段」。站内信不受这两项限制（用户主动查看，不构成打扰）。

BUG-HUB-002：早期「不传 hub 就不启用策略」让业务链路（签到奖励 / 拼团成功等）
漏传即可绕过「关闭后企微 / 邮件 / 短信一律不发」的界面承诺。现在 dispatch 在
未显式传 hub 时自己按 club_id 读 settings_hub.notify_policy，所有外发调用点一律
受控；显式 hub 仍可用于注入测试时间或强制策略。读取异常一律回落「不限制」，
延续三铁律：绝不因配置读取失败阻断业务。
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

CHANNEL_TYPES = ('wechat', 'email', 'sms', 'inapp')
TITLE_MAX = 120
BODY_MAX = 1000
_VAR_RE = re.compile(r'\{\{(\w+)\}\}')


def render_template(body: str, ctx: dict | None) -> str:
    """把 {{var}} 替换为 ctx[var]；缺失变量保留原样（不抛错，不阻断）。"""
    if not body:
        return ''
    ctx = ctx or {}

    def repl(m):
        k = m.group(1)
        return str(ctx[k]) if k in ctx else m.group(0)

    return _VAR_RE.sub(repl, body)


def normalize_channel(payload: dict, current: dict | None = None) -> dict:
    base = current if isinstance(current, dict) else {}
    t = str(payload.get('type', base.get('type') or '')).strip().lower()
    if t not in CHANNEL_TYPES:
        raise ValueError('无效通道类型（仅支持 %s）' % '/'.join(CHANNEL_TYPES))
    enabled = bool(payload.get('enabled', base.get('enabled', False)))
    cfg = payload.get('config_json')
    if not isinstance(cfg, dict):
        cfg = base.get('config_json') or {}
    return {'type': t, 'enabled': enabled, 'config_json': cfg}


def normalize_template(payload: dict, current: dict | None = None) -> dict:
    base = current if isinstance(current, dict) else {}
    code = str(payload.get('code', base.get('code') or '')).strip()
    if not code:
        raise ValueError('模板 code 必填')
    channel = str(payload.get('channel', base.get('channel') or 'inapp')).strip().lower()
    if channel not in CHANNEL_TYPES:
        channel = 'inapp'
    return {
        'code': code,
        'channel': channel,
        'title': str(payload.get('title', base.get('title') or ''))[:TITLE_MAX],
        'body': str(payload.get('body', base.get('body') or ''))[:BODY_MAX],
        'enabled': bool(payload.get('enabled', base.get('enabled', True))),
    }


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _quiet_now(hub) -> bool:
    """当前（UTC+8）是否落在免打扰区间。start==end 表示不启用；支持跨午夜。"""
    try:
        qh = (hub or {}).get('quietHours') or (None, None)
        s, e = qh[0], qh[1]
        if s is None or e is None:
            return False
        h = (hub or {}).get('hour')
        if h is None:
            h = datetime.now(timezone(timedelta(hours=8))).hour
        h, s, e = int(h) % 24, int(s) % 24, int(e) % 24
        if s == e:
            return False
        return (s <= h < e) if s < e else (h >= s or h < e)
    except Exception:
        return False


def _policy_from_db(c, club_id):
    """从设置中枢读外发策略；任何异常回落「不限制」（三铁律：不误伤业务）。

    BUG-HUB-002：由 dispatch 内部统一取策略，避免每个调用点各自记得传 hub。
    """
    try:
        from . import settings_hub as hub_mod
        # 旧库 / 单测内存库没有中枢表时按「尚未配置策略」处理：不把内置默认的
        # 22:00-08:00 免打扰强加给从未接入设置中枢的环境（既有调用零回归）。
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='settings_hub_values'").fetchone()
        if not exists:
            return {'outboundEnabled': True, 'quietHours': (None, None)}
        return hub_mod.notify_policy(c, club_id)
    except Exception:
        return {'outboundEnabled': True, 'quietHours': (None, None)}


def dispatch(c, *, club_id: int, code: str, ctx: dict | None = None,
             user_id: int | None = None, channel_override: str | None = None,
             hub: dict | None = None) -> dict:
    """执行一次通知分发。返回 {sent:bool, channel, reason}。所有异常内部吞掉，绝不外抛。

    - 先查俱乐部专属模板，再回退到平台级（club_id=0）模板；
    - 模板未启用 / 缺模板 → skipped；
    - 按模板 channel（或 channel_override）找启用中的通道配置：
        inapp → 写 inapp_messages（默认可用）；
        wechat → POST 到 club_integrations 里的企微 webhook；
        email/sms → 配置齐备才发，否则 skipped（避免无网关报错）。
    - hub（可选，设置中枢策略）：{outboundEnabled:bool, quietHours:(start,end), hour:int}。
      仅约束外发通道（wechat/email/sms），inapp 不受限。未传时由 dispatch 自己
      读 settings_hub.notify_policy，保证「不传」不等于「不受控」。
    """
    result = {'sent': False, 'channel': None, 'reason': 'no-template'}
    try:
        club_id = int(club_id)
        row = c.execute(
            """SELECT * FROM notification_templates
               WHERE code=? AND (club_id=? OR club_id=0) ORDER BY club_id DESC LIMIT 1""",
            (code, club_id)).fetchone()
        if not row or not row['enabled']:
            return result
        tpl = dict(row)
        channel = (channel_override or tpl['channel'] or 'inapp').strip().lower()
        if channel not in CHANNEL_TYPES:
            channel = 'inapp'
        result['channel'] = channel

        ch = c.execute(
            "SELECT * FROM notification_channels WHERE club_id=? AND type=? AND enabled=1 LIMIT 1",
            (club_id, channel)).fetchone()
        if channel != 'inapp' and not ch:
            result['reason'] = 'channel-disabled'
            return result

        # ── 设置中枢策略：外发总开关 + 免打扰时段，只作用于外发通道 ──
        # 未显式传 hub 时自动读库，堵住「漏传参数 = 绕过总开关」的口子。
        policy = (hub if isinstance(hub, dict)
                  else (_policy_from_db(c, club_id) if hub is None else None))
        if channel != 'inapp' and isinstance(policy, dict):
            if not policy.get('outboundEnabled', True):
                result['reason'] = 'hub-outbound-disabled'
                return result
            if _quiet_now(policy):
                result['reason'] = 'hub-quiet-hours'
                return result

        title = render_template(tpl.get('title') or '', ctx)
        body = render_template(tpl.get('body') or '', ctx)

        if channel == 'inapp':
            c.execute(
                """INSERT INTO inapp_messages(user_id, club_id, code, title, body, is_read, created_at)
                   VALUES(?,?,?,?,?,0,datetime('now'))""",
                (user_id, club_id, code, title, body))
            result['sent'] = True
            result['reason'] = 'inapp-queued'
            return result

        if channel == 'wechat':
            # 复用现有企微集成里的 webhook。三种来源按优先级探测，任一结构缺失都
            #不影响其它来源 —— 真实库 club_integrations 只有 wecom_webhook/wecom_enabled，
            #没有 type/config_json 列，直接查会抛 OperationalError 被外层吞成 exception。
            hook = ''
            try:
                cfg = loads(ch['config_json']) if ch else {}
                hook = str((cfg or {}).get('webhook') or '')
            except Exception:
                pass
            if not hook:
                try:
                    integ = c.execute(
                        "SELECT config_json FROM club_integrations WHERE club_id=? AND type='wechat' LIMIT 1",
                        (club_id,)).fetchone()
                    if integ:
                        hook = str((loads(integ['config_json']) or {}).get('webhook') or '')
                except Exception:
                    pass
            if not hook:
                try:
                    legacy = c.execute(
                        'SELECT wecom_webhook, wecom_enabled FROM club_integrations WHERE club_id=? LIMIT 1',
                        (club_id,)).fetchone()
                    if legacy and legacy['wecom_enabled']:
                        hook = str(legacy['wecom_webhook'] or '')
                except Exception:
                    pass
            if not hook:
                result['reason'] = 'wechat-no-webhook'
                return result
            try:
                data = json.dumps({'msgtype': 'text', 'text': {'content': (title + '\n' + body).strip()}},
                                  ensure_ascii=False).encode('utf-8')
                req = urllib.request.Request(hook, data=data, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    ok = resp.status == 200
                result['sent'] = ok
                result['reason'] = 'wechat-ok' if ok else 'wechat-http-%d' % resp.status
            except Exception:
                result['reason'] = 'wechat-error'
            return result

        # email / sms：配置齐备才发（此处仅做可达性占位，真实网关按 config_json 接）
        cfg = loads(ch['config_json']) if ch else {}
        if not cfg:
            result['reason'] = 'no-config'
            return result
        result['sent'] = False
        result['reason'] = 'gateway-placeholder'
        return result
    except Exception:
        result['reason'] = 'exception'
        return result
