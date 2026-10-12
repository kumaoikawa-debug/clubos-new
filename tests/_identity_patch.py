"""统一给老测试脚本补齐「参加人证件」。

背景：报名/结算链路现在强制校验 IDENTITY_FIELDS（idType + idNumber，
见 clubos_domain/participants.py；证件类型取值为中文，与 static/shared.js 的
ID_TYPES 同步）。v0.8~v0.24 的 smoke 脚本写于该规则之前，调用 signup/checkout 时
不传 participants，会被 409 挡下 —— 这是产品规则生效，不是功能回归。

本补丁只做一件事：当 POST /signup 或 /checkout 的 body 里没有 participants 时，
按 payer 信息补一位持护照的参加人。已有 participants 的调用一律不改写，
因此不会掩盖任何真实断言失败；json={} 之类的权限用例（期望 403）也不受影响
（没有 name 字段时不动，路由层权限校验先于证件校验）。
"""
ID_TYPES_FALLBACK = '护照'
ID_NUMBER_FALLBACK = 'E12345678'


def apply(client_cls=None):
    if client_cls is None:
        from fastapi.testclient import TestClient as client_cls  # noqa: N813
    orig = client_cls.post

    def post(self, url, **kw):
        body = kw.get('json')
        if isinstance(body, dict) and 'participants' not in body and (
            '/signup' in url or '/checkout' in url
        ) and body.get('name'):
            body = dict(body)
            body['participants'] = [{
                'name': body.get('name'),
                'phone': body.get('phone', ''),
                'idType': ID_TYPES_FALLBACK,
                'idNumber': ID_NUMBER_FALLBACK,
            }]
            kw['json'] = body
        return orig(self, url, **kw)

    client_cls.post = post


apply()
