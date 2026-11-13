"""设置中枢（v0.40，P1-4）：把散落在各模块的配置收敛到一个统一入口。

参考「远拓旅游 SaaS · 配置管理」的「设置中心」形态：一屏看到所有能力的开关与状态，
能一键跳转去配置，能覆盖通用偏好，并留下变更审计。

**关键设计：不搬数据、不做第二套真相。**
- 各模块的真实配置仍在各自的表里（stored_value_config / engagement_configs /
  page_layouts / notification_channels / …），中枢只读探测，绝不复制、绝不回写。
- 中枢自己只存两类东西：①通用偏好；②已接线的模块总开关（覆盖值）。
- 俱乐部覆盖 → 平台默认（club_id=0）→ 代码内置默认值，三级回落。
- 三铁律：表不存在 / SQL 异常 / 值损坏 → 静默回落到默认，绝不因配置读取失败阻断业务。

**「可切换」是严格白名单**：只有 REGISTRY 里 toggleable=True（且 app.py 已真实接线）
的模块才允许在中枢改开关。未接线的模块只展示状态，不提供假开关 —— 避免「点了没反应」。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

MODULE_PREFIX = 'module.'
PLATFORM_CLUB_ID = 0
VALUE_MAX = 200

# ── 分组 ────────────────────────────────────────────────────────────────────
GROUPS = (
    {'key': 'branding', 'label': '品牌与门户', 'order': 10},
    {'key': 'commerce', 'label': '交易与营销', 'order': 20},
    {'key': 'engagement', 'label': '互动玩法', 'order': 30},
    {'key': 'notification', 'label': '消息与通知', 'order': 40},
    {'key': 'entities', 'label': '组织与实体', 'order': 50},
)

# ── 能力注册表 ──────────────────────────────────────────────────────────────
# probe.sql 必须只带一个 ? 占位符（club_id）。mode=count → 返回整数；scalar → 转 bool。
# toggleable=True 表示 app.py 已接线（关闭会真实影响 C 端），False 表示仅只读展示。
REGISTRY = (
    {'key': 'clubBranding', 'label': '俱乐部品牌', 'group': 'branding',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/settings',
     'probe': {'sql': 'SELECT name FROM clubs WHERE id=?', 'mode': 'scalar'},
     'toggleable': False, 'note': '名称 / logo / 口号 / 联系方式'},
    {'key': 'cProfile', 'label': 'C 端「我的」页', 'group': 'branding',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/c-profile',
     'probe': {'sql': 'SELECT c_profile_json FROM clubs WHERE id=?', 'mode': 'scalar'},
     'toggleable': False, 'note': '联系我们 / 关于俱乐部文案'},
    {'key': 'mpSettings', 'label': '小程序装修', 'group': 'branding',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/mp-settings',
     'probe': {'sql': 'SELECT settings_json FROM club_mp_settings WHERE club_id=?', 'mode': 'scalar'},
     'toggleable': False, 'note': '小程序首页轮播与图文位'},
    {'key': 'pageLayout', 'label': '界面装修器', 'group': 'branding',
     'page': '/static/club/page-decorator.html', 'api': '/api/club/{club_id}/page-layouts',
     'probe': {'sql': "SELECT COUNT(*) FROM page_layouts WHERE club_id=? AND status='PUBLISHED'", 'mode': 'count'},
     'toggleable': True, 'note': '已发布版面数；关闭后 C 端回落默认首页'},

    {'key': 'campaigns', 'label': '营销活动引擎', 'group': 'commerce',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/campaigns',
     'probe': {'sql': "SELECT COUNT(*) FROM marketing_campaigns WHERE club_id=? AND status='ACTIVE'", 'mode': 'count'},
     'toggleable': False, 'note': '生效中的活动数；折扣作用在报价链路，不提供总开关'},
    {'key': 'storedValue', 'label': '会员储值', 'group': 'commerce',
     'page': '/static/club/stored-value.html', 'api': '/api/club/{club_id}/stored-value/config',
     'probe': {'sql': 'SELECT enabled FROM stored_value_config WHERE club_id=?', 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端隐藏储值入口与规则'},
    {'key': 'groupBuy', 'label': '拼团', 'group': 'commerce',
     'page': '/static/club/group-buys.html', 'api': '/api/club/{club_id}/group-buys/campaigns',
     'probe': {'sql': "SELECT COUNT(*) FROM group_buy_campaigns WHERE club_id=? AND status='ACTIVE'", 'mode': 'count'},
     'toggleable': False, 'note': '生效中的拼团活动数；在各活动里单独上下架'},
    {'key': 'affiliate', 'label': '分销员', 'group': 'commerce',
     'page': '/static/club/affiliates.html', 'api': '/api/club/{club_id}/affiliate/config',
     'probe': {'sql': 'SELECT enabled FROM affiliate_configs WHERE club_id=?', 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端隐藏推广中心、佣金不再入账（历史数据保留）'},
    {'key': 'giftCard', 'label': '礼品卡', 'group': 'commerce',
     'page': '/static/club/gift-cards.html', 'api': '/api/club/{club_id}/gift-cards/config',
     'probe': {'sql': 'SELECT enabled FROM gift_card_configs WHERE club_id=?', 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端隐藏兑换入口、兑换不再入账（历史余额保留）'},
    {'key': 'miniprogramBridge', 'label': '小程序对接', 'group': 'commerce',
     'page': '/static/club/miniprogram-bridge.html', 'api': '/api/club/{club_id}/miniprogram-bridge/config',
     'probe': {'sql': 'SELECT COALESCE((SELECT enabled FROM miniprogram_bridge_config WHERE club_id=?),1)', 'mode': 'scalar'},
     'toggleable': True, 'note': 'P2-3；关闭后小程序冷启动仍可用，但不再下发新增能力入口（资料与编排保留）'},
    {'key': 'checkoutSettlement', 'label': '结算编排', 'group': 'commerce',
     'page': '/static/club/settlements.html', 'api': '/api/club/{club_id}/settlements',
     'probe': {'sql': 'SELECT COUNT(*) FROM checkout_settlements WHERE club_id=?', 'mode': 'count'},
     'toggleable': True, 'note': 'P3；关闭后下单全部回落普通口径（已成交编排记录保留）'},

    {'key': 'checkin', 'label': '每日签到', 'group': 'engagement',
     'page': '/static/club/engagement.html', 'api': '/api/club/{club_id}/engagement/config',
     'probe': {'sql': "SELECT enabled FROM engagement_configs WHERE club_id=? AND kind='CHECKIN'", 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端规则下发为未开启'},
    {'key': 'lottery', 'label': '幸运抽奖', 'group': 'engagement',
     'page': '/static/club/engagement.html', 'api': '/api/club/{club_id}/engagement/config',
     'probe': {'sql': "SELECT enabled FROM engagement_configs WHERE club_id=? AND kind='LOTTERY'", 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端规则下发为未开启'},
    {'key': 'stamp', 'label': '集章兑换', 'group': 'engagement',
     'page': '/static/club/engagement.html', 'api': '/api/club/{club_id}/engagement/config',
     'probe': {'sql': "SELECT enabled FROM engagement_configs WHERE club_id=? AND kind='STAMP'", 'mode': 'scalar'},
     'toggleable': True, 'note': '关闭后 C 端规则下发为未开启'},

    {'key': 'notifyChannels', 'label': '通知通道', 'group': 'notification',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/notification-channels',
     'probe': {'sql': 'SELECT COUNT(*) FROM notification_channels WHERE club_id=? AND enabled=1', 'mode': 'count'},
     'toggleable': False, 'note': '启用中的通道数（企微 / 邮件 / 短信 / 站内信）'},
    {'key': 'notifyTemplates', 'label': '消息模板', 'group': 'notification',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/notification-templates',
     'probe': {'sql': 'SELECT COUNT(*) FROM notification_templates WHERE club_id=? AND enabled=1', 'mode': 'count'},
     'toggleable': False, 'note': '俱乐部级启用模板数（另有平台级兜底）'},
    {'key': 'inappMessages', 'label': '站内信收件箱', 'group': 'notification',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/inapp-messages',
     'probe': {'sql': 'SELECT COUNT(*) FROM inapp_messages WHERE club_id=?', 'mode': 'count'},
     'toggleable': False, 'note': '累计站内信条数'},

    {'key': 'stores', 'label': '门店 / 基地', 'group': 'entities',
     'page': '/static/club/stores.html', 'api': '/api/club/{club_id}/stores',
     'probe': {'sql': "SELECT COUNT(*) FROM stores WHERE club_id=? AND status='ACTIVE'", 'mode': 'count'},
     'toggleable': False, 'note': '营业中的门店数'},
    {'key': 'members', 'label': '俱乐部会员', 'group': 'entities',
     'page': '/static/club/index.html', 'api': '/api/club/{club_id}/members',
     'probe': {'sql': 'SELECT COUNT(*) FROM club_members WHERE club_id=?', 'mode': 'count'},
     'toggleable': False, 'note': '会员总数'},
)

# ── 通用偏好（每一项都有真实消费点，不设摆设字段）────────────────────────────
PREFERENCES = (
    {'key': 'portal.homeLayoutMode', 'label': '首页呈现方式', 'group': 'branding',
     'type': 'enum', 'options': ('decorated', 'default'), 'default': 'decorated',
     'note': 'decorated=用装修版面；default=强制回落系统默认首页'},
    {'key': 'notify.outboundEnabled', 'label': '允许外发通知', 'group': 'notification',
     'type': 'bool', 'default': True,
     'note': '关闭后企微 / 邮件 / 短信一律不发，站内信仍写入'},
    {'key': 'notify.quietHoursStart', 'label': '免打扰开始（点）', 'group': 'notification',
     'type': 'int', 'min': 0, 'max': 23, 'default': 22, 'note': 'UTC+8；与结束相同表示不启用'},
    {'key': 'notify.quietHoursEnd', 'label': '免打扰结束（点）', 'group': 'notification',
     'type': 'int', 'min': 0, 'max': 23, 'default': 8, 'note': 'UTC+8；支持跨午夜区间'},
)

MODULE_BY_KEY = {m['key']: m for m in REGISTRY}
PREF_BY_KEY = {p['key']: p for p in PREFERENCES}
TOGGLEABLE = tuple(m['key'] for m in REGISTRY if m['toggleable'])


def dumps(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def loads(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _decode(raw):
    """返回 (value, ok)。空 / 损坏 → ok=False，便于三级回落。"""
    if raw is None:
        return None, False
    try:
        return json.loads(raw), True
    except (TypeError, ValueError):
        return None, False


def now_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def biz_hour() -> int:
    """固定 UTC+8 业务小时（与互动玩法业务日口径一致）。"""
    return datetime.now(timezone(timedelta(hours=8))).hour


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """免打扰判定：start==end 表示不启用；支持跨午夜（22→8）。"""
    try:
        h, s, e = int(hour) % 24, int(start) % 24, int(end) % 24
    except (TypeError, ValueError):
        return False
    if s == e:
        return False
    if s < e:
        return s <= h < e
    return h >= s or h < e


# ── 校验 ────────────────────────────────────────────────────────────────────
def spec_for(key: str):
    """返回该项的校验规格；未知 key / 未接线的模块开关 → None（拒绝写入）。"""
    key = str(key or '').strip()
    if key.startswith(MODULE_PREFIX):
        mk = key[len(MODULE_PREFIX):]
        m = MODULE_BY_KEY.get(mk)
        if not m or not m['toggleable']:
            return None
        return {'key': key, 'type': 'bool', 'default': True, 'kind': 'module'}
    p = PREF_BY_KEY.get(key)
    if p:
        return dict(p, kind='preference')
    return None


def normalize_value(spec: dict, value):
    """按规格校验并归一化；非法值抛 ValueError（由路由转 400）。"""
    t = spec['type']
    if t == 'bool':
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ('true', '1', 'yes', 'on'):
                return True
            if v in ('false', '0', 'no', 'off'):
                return False
            raise ValueError('%s 必须是布尔值' % spec['key'])
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        raise ValueError('%s 必须是布尔值' % spec['key'])
    if t == 'int':
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError('%s 必须是整数' % spec['key'])
        lo, hi = int(spec.get('min', 0)), int(spec.get('max', 999999))
        # 不用夹逼：越界直接拒绝，避免把非法输入悄悄洗成「看起来合法」的值
        if v < lo or v > hi:
            raise ValueError('%s 超出范围 %d~%d' % (spec['key'], lo, hi))
        return v
    if t == 'enum':
        v = str(value or '').strip()
        if v not in tuple(spec.get('options') or ()):
            raise ValueError('%s 仅支持 %s' % (spec['key'], '/'.join(spec.get('options') or ())))
        return v
    if t == 'str':
        return str(value or '')[:VALUE_MAX]
    raise ValueError('未知设置类型 %s' % t)


def normalize_changes(payload) -> dict:
    """把 {changes:{...}} 或裸 {...} 归一化为 {key: value}；未知 key 直接拒绝。"""
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是对象')
    changes = payload.get('changes') if isinstance(payload.get('changes'), dict) else payload
    out = {}
    for k, v in changes.items():
        if k in ('changes', 'note', 'operator'):
            continue
        spec = spec_for(k)
        if spec is None:
            raise ValueError('未知或不可切换的设置项：%s' % k)
        out[spec['key']] = normalize_value(spec, v)
    if not out:
        raise ValueError('没有需要更新的设置项')
    return out


# ── 读写（俱乐部覆盖 → 平台默认 → 内置默认）────────────────────────────────
def get(c, club_id: int, key: str) -> dict:
    """返回 {value, default, overridden, scope}；表缺失/异常一律回落内置默认。"""
    spec = spec_for(key) or PREF_BY_KEY.get(str(key)) or {'default': None}
    default = spec.get('default')
    out = {'key': key, 'value': default, 'default': default,
           'overridden': False, 'scope': 'default'}
    try:
        r = c.execute(
            """SELECT club_id, value_json FROM settings_hub_values
               WHERE key=? AND club_id IN (?, ?) ORDER BY club_id DESC LIMIT 1""",
            (str(key), int(club_id), PLATFORM_CLUB_ID)).fetchone()
        if r:
            v, ok = _decode(r['value_json'] if hasattr(r, 'keys') else r[1])
            if ok:
                cid = r['club_id'] if hasattr(r, 'keys') else r[0]
                out['value'] = v
                out['overridden'] = True
                out['scope'] = 'club' if int(cid) != PLATFORM_CLUB_ID else 'platform'
    except Exception:
        pass
    return out


def is_module_enabled(c, club_id: int, module_key: str) -> bool:
    """给各模块接线用的总开关。异常 / 未覆盖 → True（三铁律：绝不误关业务）。"""
    try:
        return bool(get(c, club_id, MODULE_PREFIX + str(module_key))['value'])
    except Exception:
        return True


def _probe(c, club_id: int, item: dict):
    p = item.get('probe') or {}
    try:
        r = c.execute(p['sql'], (int(club_id),)).fetchone()
        if p['mode'] == 'count':
            return {'count': int(r[0] or 0) if r else 0, 'configured': bool(r and int(r[0] or 0) > 0)}
        v = r[0] if r else None
        return {'count': None, 'configured': bool(v) if v is not None else False}
    except Exception:
        # 旧库 / 表还没建 / 字段缺失 → 状态未知，不编造
        return {'count': None, 'configured': None}


def snapshot(c, club_id: int) -> dict:
    """一屏全景：分组 + 模块真实状态 + 偏好生效值 + 汇总。全只读、异常安全。"""
    modules = []
    for m in REGISTRY:
        st = _probe(c, club_id, m)
        hub = get(c, club_id, MODULE_PREFIX + m['key']) if m['toggleable'] else None
        hub_on = bool(hub['value']) if hub else True
        modules.append({
            'key': m['key'], 'label': m['label'], 'group': m['group'],
            'page': m['page'], 'api': m['api'], 'note': m['note'],
            'toggleable': bool(m['toggleable']),
            'configured': st['configured'], 'count': st['count'],
            'hubEnabled': hub_on if m['toggleable'] else None,
            'hubOverridden': hub['overridden'] if hub else False,
            'effectiveEnabled': (st['configured'] is not False) and hub_on,
        })
    prefs = []
    for p in PREFERENCES:
        g = get(c, club_id, p['key'])
        prefs.append({
            'key': p['key'], 'label': p['label'], 'group': p['group'],
            'type': p['type'], 'options': list(p.get('options') or ()),
            'default': p['default'], 'value': g['value'],
            'overridden': g['overridden'], 'scope': g['scope'], 'note': p['note'],
            **({'min': p['min'], 'max': p['max']} if p['type'] == 'int' else {}),
        })
    groups = []
    for g in sorted(GROUPS, key=lambda x: x['order']):
        groups.append({'key': g['key'], 'label': g['label'],
                       'modules': [m['key'] for m in REGISTRY if m['group'] == g['key']]})
    return {
        'clubId': int(club_id),
        'groups': groups,
        'modules': modules,
        'preferences': prefs,
        'summary': {
            'totalModules': len(modules),
            'configuredModules': sum(1 for m in modules if m['configured'] is True),
            'unknownModules': sum(1 for m in modules if m['configured'] is None),
            'disabledByHub': sum(1 for m in modules if m['toggleable'] and not m['hubEnabled']),
            'overriddenPreferences': sum(1 for p in prefs if p['overridden']),
        },
    }


def registry_view() -> dict:
    """注册表（给后台渲染表单用），不含任何 SQL 细节。"""
    return {
        'groups': [dict(g) for g in GROUPS],
        'modules': [{k: m[k] for k in ('key', 'label', 'group', 'page', 'api', 'toggleable', 'note')}
                    for m in REGISTRY],
        'preferences': [{k: p.get(k) for k in ('key', 'label', 'group', 'type', 'options',
                                               'default', 'min', 'max', 'note')}
                        for p in PREFERENCES],
        'toggleableModules': list(TOGGLEABLE),
    }


def set_many(c, club_id: int, changes: dict, operator: str = '', note: str = '') -> dict:
    """批量写入覆盖值 + 审计。调用方负责事务；值校验失败抛 ValueError。"""
    norm = {}
    for k, v in changes.items():
        spec = spec_for(k)
        if spec is None:
            raise ValueError('未知或不可切换的设置项：%s' % k)
        norm[spec['key']] = normalize_value(spec, v)
    if not norm:
        raise ValueError('没有需要更新的设置项')
    updated, skipped = [], []
    for k, v in norm.items():
        try:
            r = c.execute('SELECT value_json FROM settings_hub_values WHERE club_id=? AND key=?',
                          (int(club_id), k)).fetchone()
            old_raw = (r['value_json'] if hasattr(r, 'keys') else r[0]) if r else None
            old, ok = _decode(old_raw)
            if ok and old == v:
                skipped.append(k)
                continue
            c.execute("""INSERT INTO settings_hub_values(club_id,key,value_json,updated_at)
                         VALUES(?,?,?,?) ON CONFLICT(club_id,key)
                         DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                      (int(club_id), k, dumps(v), now_str()))
            c.execute("""INSERT INTO settings_hub_audit
                         (club_id,key,old_value_json,new_value_json,action,operator,note,created_at)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (int(club_id), k, old_raw, dumps(v), 'SET',
                       str(operator or '')[:60], str(note or '')[:VALUE_MAX], now_str()))
            updated.append(k)
        except Exception:
            # 单个 key 写失败不影响其它 key；由调用方事务决定最终一致性
            skipped.append(k)
    return {'updated': updated, 'skipped': skipped}


def reset(c, club_id: int, keys) -> dict:
    """删除俱乐部级覆盖 → 回落平台默认 / 内置默认。keys 为 None 时重置全部允许项。"""
    allowed = list(PREF_BY_KEY.keys()) + [MODULE_PREFIX + k for k in TOGGLEABLE]
    targets = [str(k) for k in keys] if keys else allowed
    invalid = [k for k in targets if k not in allowed]
    if invalid:
        raise ValueError('未知或不可重置的设置项：%s' % ','.join(invalid))
    done = []
    for k in targets:
        try:
            r = c.execute('SELECT value_json FROM settings_hub_values WHERE club_id=? AND key=?',
                          (int(club_id), k)).fetchone()
            if not r:
                continue
            old_raw = r['value_json'] if hasattr(r, 'keys') else r[0]
            c.execute('DELETE FROM settings_hub_values WHERE club_id=? AND key=?', (int(club_id), k))
            c.execute("""INSERT INTO settings_hub_audit
                         (club_id,key,old_value_json,new_value_json,action,operator,note,created_at)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (int(club_id), k, old_raw, None, 'RESET', '', '', now_str()))
            done.append(k)
        except Exception:
            continue
    return {'reset': done}


def audit_list(c, club_id: int, limit: int = 50) -> list:
    try:
        rs = c.execute("""SELECT * FROM settings_hub_audit WHERE club_id=?
                          ORDER BY id DESC LIMIT ?""", (int(club_id), max(1, min(200, int(limit))))).fetchall()
        out = []
        for r in rs:
            d = dict(r) if hasattr(r, 'keys') else {}
            if not d:
                continue
            out.append({
                'id': d.get('id'), 'key': d.get('key'),
                'oldValue': _decode(d.get('old_value_json'))[0],
                'newValue': _decode(d.get('new_value_json'))[0],
                'action': d.get('action'), 'operator': d.get('operator'),
                'note': d.get('note'), 'createdAt': d.get('created_at'),
            })
        return out
    except Exception:
        return []


# ── 供 app.py 接线的两个策略读取器（纯读、异常吞掉）────────────────────────
def portal_policy(c, club_id: int) -> dict:
    """C 端门户策略：装修版面是否允许呈现。"""
    try:
        return {
            'homeLayoutMode': get(c, club_id, 'portal.homeLayoutMode')['value'],
            'decoratorEnabled': is_module_enabled(c, club_id, 'pageLayout'),
            'storedValueEnabled': is_module_enabled(c, club_id, 'storedValue'),
            'engagementEnabled': {k: is_module_enabled(c, club_id, k)
                                  for k in ('checkin', 'lottery', 'stamp')},
        }
    except Exception:
        return {'homeLayoutMode': 'decorated', 'decoratorEnabled': True,
                'storedValueEnabled': True,
                'engagementEnabled': {'checkin': True, 'lottery': True, 'stamp': True}}


def notify_policy(c, club_id: int) -> dict:
    """外发通知策略，供 notifications.dispatch(hub=...) 使用。"""
    try:
        return {
            'outboundEnabled': bool(get(c, club_id, 'notify.outboundEnabled')['value']),
            'quietHours': (int(get(c, club_id, 'notify.quietHoursStart')['value']),
                           int(get(c, club_id, 'notify.quietHoursEnd')['value'])),
            'hour': biz_hour(),
        }
    except Exception:
        return {'outboundEnabled': True, 'quietHours': (None, None), 'hour': biz_hour()}
