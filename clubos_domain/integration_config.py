"""外部系统接口配置（2026-11-15）。

把「上游接口要填什么」从代码里搬到总平台后台的固定配置页：地址、路径、
请求方法、令牌字段名、令牌、额外请求头、超时、返回字段映射都由运营在页面上
填写并持久化；上游换地址 / 换路径 / 换字段名时只改配置，不需要改代码、不需要发版。

设计约束
--------
* 不新增表：复用 ``platform_settings(key TEXT PRIMARY KEY, value TEXT)`` 存 JSON，
  存量库零迁移；所有函数只接收调用方传入的 sqlite cursor，模块本身不 import db。
* 密钥不回显：``public()`` 一律把令牌换成掩码；明文只在服务端 ``effective()``
  内部使用，绝不写进任何 API 响应、日志或前端页面。
* 表单留空＝保持原值：前端每次保存都提交整份表单，令牌字段留空时不覆盖已存令牌；
  需要清除必须显式 ``clearToken=true``。
* 缺省回落：后台没配 / 关掉开关时，用内置默认 + 环境变量，保证旧实例行为不变。
* SSRF 护栏：origin 必须是 http/https、无 userinfo / 查询串；公网一律要求 https，
  仅本机允许 http；私网地址段拒绝。
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

DB_KEY_PREFIX = 'integration.api.'

PEAKTRACE = 'peaktrace-catalog'

# 内置默认 = 改动前代码里的硬编码值，保证「没配过」时行为完全一致。
DEFAULTS: dict[str, dict] = {
    PEAKTRACE: {
        'enabled': True,
        'origin': os.environ.get('PEAKTRACE_ORIGIN', 'https://peaktrace-h5.app.workbuddy.host').rstrip('/'),
        'timeoutSeconds': 20,
        'catalogPath': '/api/b2b/catalog',
        'productsPath': '/api/products',
        'categoriesPath': '/api/categories',
        'inventoryPath': '/api/admin/inventory/get',
        'inventoryMethod': 'POST',
        'tokenField': 'token',
        'token': '',
        'extraHeaders': {},
        'productsKey': 'products',
        'categoriesKey': 'categories',
        'skusKey': 'skus',
        'availableKey': 'available',
        'notes': '',
    }
}

# 后台固定配置页由这份元数据驱动渲染：新增字段只改这里 + DEFAULTS，前端不用改结构。
FIELD_SPECS: dict[str, list[dict]] = {
    PEAKTRACE: [
        {'key': 'enabled', 'label': '启用自定义接口配置', 'type': 'bool', 'group': '① 基础',
         'help': '关闭后同步会回到系统内置默认接口，已填写的内容保留不删除。'},
        {'key': 'origin', 'label': '上游服务地址 Origin', 'type': 'text', 'group': '① 基础',
         'placeholder': 'https://peaktrace-h5.app.workbuddy.host',
         'help': '例：https://supplier.example.com。只填域名，结尾不要带 /，也不要带路径；公网必须 https。'},
        {'key': 'timeoutSeconds', 'label': '请求超时（秒）', 'type': 'number', 'group': '① 基础',
         'min': 1, 'max': 120, 'help': '上游无响应时最多等待多少秒，建议 10～30。'},
        {'key': 'catalogPath', 'label': '商品总目录接口（优先）', 'type': 'text', 'group': '② 接口路径',
         'help': 'GET。只填路径，如 /api/b2b/catalog。返回 JSON 要同时含 products 与 categories 两个数组（字段名可在④修改）。'},
        {'key': 'productsPath', 'label': '商品列表接口（兼容回退）', 'type': 'text', 'group': '② 接口路径',
         'help': 'GET。只填路径。总目录接口不可用（上游是旧版本）时才用这个，返回商品数组。'},
        {'key': 'categoriesPath', 'label': '分类列表接口（兼容回退）', 'type': 'text', 'group': '② 接口路径',
         'help': 'GET。只填路径。与商品列表接口配套，返回分类数组，如 [{"id":"1","name":"冲锋衣"}]。'},
        {'key': 'inventoryPath', 'label': '库存接口（兼容回退）', 'type': 'text', 'group': '② 接口路径',
         'help': '只填路径。返回 {"商品id|尺码": 数量} 或 {"商品id|尺码":{"available":数量}}；读不到库存按 0，不阻断商品同步。'},
        {'key': 'inventoryMethod', 'label': '库存接口请求方法', 'type': 'select', 'group': '② 接口路径',
         'options': [{'value': 'POST', 'label': 'POST（令牌放 JSON body）'},
                     {'value': 'GET', 'label': 'GET（令牌放查询串）'}],
         'help': '托管网关会剥掉自定义请求头，令牌通常只能放 body 或查询串。'},
        {'key': 'tokenField', 'label': '令牌参数名', 'type': 'text', 'group': '③ 鉴权',
         'help': '令牌放进请求时的字段名，默认 token。只能用字母、数字、下划线、点、连字符；填 token 就是 POST {"token":"..."}。'},
        {'key': 'token', 'label': '接口令牌', 'type': 'secret', 'group': '③ 鉴权',
         'placeholder': '不修改请留空', 'help': '只保存在服务端数据库，页面永远只显示掩码。留空＝保持当前值。'},
        {'key': 'clearToken', 'label': '清除已保存的令牌', 'type': 'bool', 'group': '③ 鉴权',
         'help': '勾选后清空令牌，之后回落到服务器环境变量 PEAKTRACE_API_TOKEN。'},
        {'key': 'extraHeaders', 'label': '额外请求头（JSON 对象）', 'type': 'json', 'group': '③ 鉴权',
         'placeholder': '{"X-Vendor": "clubos"}',
         'help': '可留空填 {}。例：{"X-Vendor":"clubos"}。Host / Content-Length 会被忽略；请求头值会明文回显后台，高敏令牌请填上面的「接口令牌」。'},
        {'key': 'productsKey', 'label': '商品数组字段名', 'type': 'text', 'group': '④ 返回字段映射',
         'help': '上游把商品数组改名时，在这里填新字段名，无需改代码。'},
        {'key': 'categoriesKey', 'label': '分类数组字段名', 'type': 'text', 'group': '④ 返回字段映射'},
        {'key': 'skusKey', 'label': '规格数组字段名', 'type': 'text', 'group': '④ 返回字段映射',
         'help': '商品对象里规格列表的字段名，默认 skus。'},
        {'key': 'availableKey', 'label': '可售库存字段名', 'type': 'text', 'group': '④ 返回字段映射',
         'help': '规格对象里可售数量的字段名，默认 available。'},
        {'key': 'notes', 'label': '备注（仅后台可见）', 'type': 'textarea', 'group': '⑤ 其他',
         'placeholder': '例如：上游 2026-12 换域名，由张三更新', 'help': '记录本次变更原因，方便交接。'},
    ]
}

# 分组一句话说明：让运营先知道「这一组到底在解决什么」，再填具体字段。
GROUP_GUIDE = {
    '① 基础': '先确定上游域名与超时。这里只决定「去哪个服务器取数」，不填接口路径。',
    '② 接口路径': '所有路径都只填以 / 开头的部分，系统会自动拼到上面的域名后面。总目录能用就优先用总目录。',
    '③ 鉴权': '告诉系统令牌叫什么、令牌是什么。留空＝不修改已保存令牌；高敏令牌不要放请求头。',
    '④ 返回字段映射': '只有当上游返回的字段名和系统默认的 products/categories/skus/available 不一样时才需要改。',
    '⑤ 其他': '给交接留一句话，说明这次是谁因为什么改的。',
}

# 标准数据说明：页面顶部折叠卡直接展示，避免用户不知道「上游到底该吐什么 JSON」。
# {origin}/{catalogPath} 等占位符由前端按当前配置实时替换；令牌只显示 <令牌>，不回显明文。
STANDARD_GUIDE = {
    'title': '标准数据说明（先看这里，再填下面）',
    'intro': 'ClubOS 只读取上游的商品、分类、库存三类数据，不改上游任何内容。按下面 3 步填，填完点「测试连接」，通过后再保存。',
    'steps': [
        {'title': '第 1 步：填域名', 'text': '「上游服务地址 Origin」只填 https://域名，结尾不要带 /，也不要带接口路径。'},
        {'title': '第 2 步：填路径', 'text': '「接口路径」只填 /api/... 这一段。优先用总目录接口一次拿齐商品+分类；没有总目录再填兼容接口。'},
        {'title': '第 3 步：填鉴权', 'text': '令牌参数名一般是 token；令牌只在「接口令牌」里填一次，页面之后只显示掩码。'},
    ],
    'examples': [
        {
            'title': '① 商品总目录（优先）：一次拿齐商品 + 分类 + 库存',
            'request': 'GET {origin}{catalogPath}',
            'response': '''{
  "products": [
    {"id":"P001","name":"轻量冲锋衣","price":599,"category":"1",
     "image":"/img/p001.jpg","gallery":["/img/p001-b.jpg"],
     "sizes":["M","L"],"spec":"均码","sub":"轻量防水","desc":"适合春夏",
     "fabric":"GORE-TEX","catalog_view":[1200,800,0,0,1200,800],
     "skus":[{"sku":"P001|M","available":12},{"sku":"P001|L","available":3}]}
  ],
  "categories": [{"id":"1","name":"冲锋衣"}]
}''',
            'note': '两个数组必须同时存在且字段名与「④ 返回字段映射」一致；skus/available 读不到只会把库存按 0 处理，不阻断商品同步。',
        },
        {
            'title': '② 兼容回退：商品列表 + 分类列表（总目录不可用才填）',
            'request': 'GET {origin}{productsPath}　/　GET {origin}{categoriesPath}',
            'response': '''# 商品列表：数组
[{"id":"P001","name":"轻量冲锋衣","price":599,"category":"1",
  "image":"/img/p001.jpg","gallery":[],"sizes":["M","L"]}]
# 分类列表：数组
[{"id":"1","name":"冲锋衣"}]''',
            'note': '商品 id / name / price 必填；图片可用相对路径，系统会自动补域名。尺码没有时按 spec 建一个「均码」。',
        },
        {
            'title': '③ 库存接口（兼容回退）：按 SKU 返回可售量',
            'request': '{inventoryMethod} {origin}{inventoryPath}　令牌放 JSON body（POST）或查询串（GET），字段名 = {tokenField}',
            'response': '''{
  "P001|M": 12,
  "P001|L": {"available": 3}
}''',
            'note': 'key 必须与商品的「上游 id|尺码」完全一致；值是数字或含 available 字段的对象。读不到整个接口时商品仍会先同步，库存按 0。',
        },
    ],
    'rules': [
        '路径字段只填路径，不要把域名再写一遍；写了域名会保存失败。',
        'extraHeaders 必须是 JSON 对象，例如 {"X-Vendor":"clubos"}；空就填 {}。',
        '字段名映射只有上游改了 JSON key 才动；乱改会导致测试连接显示 0 商品。',
        '测试连接只读不写；通过后再点「保存配置」，下一次同步立即按新配置执行。',
    ],
}

_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]*$')
_FORBIDDEN_HEADERS = {'host', 'content-length', 'connection', 'transfer-encoding'}
_PRIVATE_HOST = re.compile(r'^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|::1$|fc|fd)')


class ConfigError(ValueError):
    """配置校验失败（路由层转 400）。"""


def _dbkey(key: str) -> str:
    return DB_KEY_PREFIX + key


def defaults(key: str = PEAKTRACE) -> dict:
    if key not in DEFAULTS:
        raise ConfigError('未知的接口配置：%s' % key)
    return copy.deepcopy(DEFAULTS[key])


def mask_token(token: str | None) -> str:
    t = str(token or '')
    if not t:
        return ''
    if len(t) <= 8:
        return '*' * len(t)
    return t[:2] + '****' + t[-4:]


def _validate_origin(value) -> str:
    s = str(value or '').strip().rstrip('/')
    if not s:
        raise ConfigError('上游服务地址不能为空')
    try:
        sp = urlsplit(s)
    except Exception:
        raise ConfigError('上游服务地址格式不正确')
    if sp.scheme not in ('http', 'https') or not sp.hostname:
        raise ConfigError('上游服务地址必须是完整的 http(s) 地址')
    if sp.username or sp.password or sp.query or sp.fragment:
        raise ConfigError('上游服务地址不能带账号密码、查询串或锚点')
    host = sp.hostname.lower()
    local = host in ('localhost', '127.0.0.1', '::1')
    # 先判内网再判协议，避免 http://10.x 报「必须 https」这种误导性提示。
    if _PRIVATE_HOST.match(host) and not local:
        raise ConfigError('不允许填写内网地址')
    if sp.scheme == 'http' and not local:
        raise ConfigError('公网地址必须使用 https（本机联调可用 http）')
    return '%s://%s%s' % (sp.scheme, sp.netloc, sp.path.rstrip('/'))


def _validate_path(value, label: str) -> str:
    s = str(value or '').strip()
    if not s.startswith('/'):
        raise ConfigError('%s 必须以 / 开头' % label)
    if '://' in s or any(ch.isspace() for ch in s):
        raise ConfigError('%s 不是合法的路径' % label)
    return s


def _validate_ident(value, label: str) -> str:
    s = str(value or '').strip()
    if not _IDENT.match(s):
        raise ConfigError('%s 只能是字母 / 数字 / 下划线 / 点 / 连字符' % label)
    return s


def _clean_headers(value) -> dict:
    if value in (None, '', {}):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise ConfigError('额外请求头必须是 JSON 对象，例如 {"X-Vendor":"clubos"}')
    if not isinstance(value, dict):
        raise ConfigError('额外请求头必须是 JSON 对象')
    out = {}
    for k, v in value.items():
        k = str(k).strip()
        if k.lower() in _FORBIDDEN_HEADERS:
            continue
        if not k or not re.match(r'^[A-Za-z0-9_-]+$', k):
            raise ConfigError('请求头名称不合法：%s' % k)
        out[k] = str(v)
    return out


def load(c, key: str = PEAKTRACE) -> tuple[dict, dict]:
    """读取已保存配置。返回 (配置明文, 元信息)。

    明文只在服务端使用；路由层对外必须走 ``public()``。
    """
    cfg = defaults(key)
    meta = {'source': 'default', 'updatedAt': None, 'hasSaved': False}
    try:
        r = c.execute('SELECT value FROM platform_settings WHERE key=?', (_dbkey(key),)).fetchone()
    except Exception:
        return cfg, meta                      # 单测内存库没建表时安静回落
    if not r:
        return cfg, meta
    raw = r['value'] if isinstance(r, dict) else r[0]
    try:
        saved = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return cfg, meta
    if not isinstance(saved, dict):
        return cfg, meta
    for k in cfg:
        if k in saved and k not in ('token',):
            cfg[k] = saved[k]
    cfg['token'] = str(saved.get('token') or '')
    meta.update(source='database', hasSaved=True,
                updatedAt=saved.get('_updatedAt'),
                notes=saved.get('notes') if 'notes' in saved else None)
    return cfg, meta


def normalize(c, payload: dict, key: str = PEAKTRACE) -> dict:
    """校验并归一化一次页面提交。返回可直接落库的明文配置（不含 clearToken）。"""
    old, _ = load(c, key)
    p = payload if isinstance(payload, dict) else {}
    cfg = defaults(key)
    cfg['enabled'] = bool(p.get('enabled', old.get('enabled', True)))
    cfg['origin'] = _validate_origin(p.get('origin', old.get('origin')))
    try:
        timeout = int(p.get('timeoutSeconds', old.get('timeoutSeconds', 20)))
    except (TypeError, ValueError):
        raise ConfigError('请求超时必须是整数秒')
    if not 1 <= timeout <= 120:
        raise ConfigError('请求超时必须在 1～120 秒之间')
    cfg['timeoutSeconds'] = timeout
    for f, label in (('catalogPath', '商品总目录接口'), ('productsPath', '商品列表接口'),
                     ('categoriesPath', '分类列表接口'), ('inventoryPath', '库存接口')):
        cfg[f] = _validate_path(p.get(f, old.get(f)), label)
    method = str(p.get('inventoryMethod', old.get('inventoryMethod', 'POST'))).upper()
    if method not in ('GET', 'POST'):
        raise ConfigError('库存接口请求方法只能是 GET 或 POST')
    cfg['inventoryMethod'] = method
    cfg['tokenField'] = _validate_ident(p.get('tokenField', old.get('tokenField')), '令牌参数名')
    cfg['productsKey'] = _validate_ident(p.get('productsKey', old.get('productsKey')), '商品数组字段名')
    cfg['categoriesKey'] = _validate_ident(p.get('categoriesKey', old.get('categoriesKey')), '分类数组字段名')
    cfg['skusKey'] = _validate_ident(p.get('skusKey', old.get('skusKey')), '规格数组字段名')
    cfg['availableKey'] = _validate_ident(p.get('availableKey', old.get('availableKey')), '可售库存字段名')
    cfg['extraHeaders'] = _clean_headers(p.get('extraHeaders', old.get('extraHeaders')))
    cfg['notes'] = str(p.get('notes') or '').strip()[:500]

    # 令牌：留空 = 保持；显式 clearToken = 清空；其它 = 覆盖。
    incoming = p.get('token')
    if p.get('clearToken'):
        cfg['token'] = ''
    elif incoming is None or str(incoming).strip() == '' or str(incoming) == mask_token(old.get('token')):
        cfg['token'] = str(old.get('token') or '')
    else:
        cfg['token'] = str(incoming).strip()[:512]
    return cfg


def save(c, payload: dict, key: str = PEAKTRACE, operator: str = '') -> dict:
    cfg = normalize(c, payload, key)
    stored = dict(cfg)
    stored['_updatedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stored['_updatedBy'] = str(operator or '')[:80]
    c.execute('INSERT INTO platform_settings(key,value) VALUES(?,?) '
              'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (_dbkey(key), json.dumps(stored, ensure_ascii=False)))
    return cfg


def reset(c, key: str = PEAKTRACE) -> None:
    c.execute('DELETE FROM platform_settings WHERE key=?', (_dbkey(key),))


def public(c, key: str = PEAKTRACE) -> dict:
    """给后台页面的数据：字段元数据 + 配置值（令牌掩码）+ 默认参考值。"""
    cfg, meta = load(c, key)
    view = {k: v for k, v in cfg.items() if k != 'token'}
    view['token'] = mask_token(cfg.get('token'))     # 只给掩码，不给长度以外的信息
    view['clearToken'] = False
    dft = defaults(key)
    return {
        'key': key,
        'label': 'PEAKTRACE 商品目录同步',
        'description': '上游接口的地址、路径、鉴权与返回字段都在这里填写。保存后点「同步 PEAKTRACE」或等待自动同步即按新配置执行，不需要改代码或重新发布。',
        'fields': FIELD_SPECS[key],
        'groupGuide': GROUP_GUIDE,
        'standardGuide': STANDARD_GUIDE,
        'config': view,
        'defaults': {k: v for k, v in dft.items() if k != 'token'},
        'hasToken': bool(cfg.get('token')),
        'source': meta['source'],
        'updatedAt': meta['updatedAt'],
        'envTokenFallback': bool(os.environ.get('PEAKTRACE_API_TOKEN')),
    }


def effective(c=None, key: str = PEAKTRACE, config: dict | None = None) -> dict:
    """给请求层用的最终配置（含明文令牌）。

    ``config`` 优先（测试连接时用页面草稿），否则从库里读；未启用自定义配置时
    整体回落内置默认。令牌为空时回落环境变量 PEAKTRACE_API_TOKEN。
    """
    if config is not None:
        cfg = copy.deepcopy(config)
    elif c is not None:
        cfg, _ = load(c, key)
    else:
        cfg = defaults(key)
    if not cfg.get('enabled', True):
        cfg = defaults(key)
    cfg['origin'] = str(cfg.get('origin') or DEFAULTS[key]['origin']).rstrip('/')
    if not cfg.get('token'):
        cfg['token'] = os.environ.get('PEAKTRACE_API_TOKEN', '') or 'peaktrace-admin'
    cfg.setdefault('extraHeaders', {})
    return cfg
