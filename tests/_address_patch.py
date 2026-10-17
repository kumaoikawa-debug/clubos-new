"""统一给老测试脚本补齐「收货地址」。

背景：装备是实物、由平台统一发货，结算单必须落到一个真实存在的收货地址
（app.py 的 _gear_address_snapshot：只认地址簿 id 且按 user_id 校验归属）。
v0.8~v0.24 的 smoke 脚本写于该规则之前，调 gear-checkout 时不传 addressId，
会被 400「请先添加收货地址」挡下 —— 这是产品规则生效，不是功能回归。

本补丁只做一件事：POST /api/public/clubs/{id}/gear-checkout 的 body 里
没有 addressId 且带 items 时，先为同一个 userId 确保存在一个测试地址
（GET 列表，空则 POST 建一个），再把地址 id 补进 body。已有 addressId 的
调用一律不改写；items 为空之类的权限/参数用例（期望 400/403）也不受影响
——那些调用在校验链上先于地址解析返回，本来就走不到那一步。
"""
DEFAULT_USER_ID = 1
TEST_ADDRESS = {
    'consignee': '测试用户',
    'phone': '13900000099',
    'province': '四川省',
    'city': '成都市',
    'district': '武侯区',
    'detail': '天府大道北段 966 号天府软件园 C 区 3 栋 1201',
}


def _ensure_address(client, user_id):
    """返回该 user 的一个地址 id：优先已存在的，否则新建。"""
    try:
        lst = client.get(f'/api/public/users/{user_id}/addresses')
        if lst.status_code < 400:
            items = lst.json() or []
            if items:
                return items[0]['id']
    except Exception:
        pass
    r = client.post(f'/api/public/users/{user_id}/addresses', json=dict(TEST_ADDRESS))
    assert r.status_code < 400, ('seed address failed', r.status_code, r.text)
    return r.json()['id']


def apply(client_cls=None):
    if client_cls is None:
        from fastapi.testclient import TestClient as client_cls  # noqa: N813
    orig = client_cls.post

    def post(self, url, **kw):
        body = kw.get('json')
        if (
            isinstance(body, dict)
            and 'gear-checkout' in url
            and body.get('items')
            and not body.get('addressId')
        ):
            uid = int(body.get('userId') or DEFAULT_USER_ID)
            body = dict(body)
            body['addressId'] = _ensure_address(self, uid)
            kw['json'] = body
        return orig(self, url, **kw)

    client_cls.post = post


apply()
