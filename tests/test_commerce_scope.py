"""营销适用范围硬边界单测（2026-11-13 用户明确：只覆盖俱乐部活动/营地产品）。

验证：装备订单类型一律拒绝、别名绕过无效、三处白名单常量一致、首单资格
不再引用 gear_orders / member_orders。
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DOMAIN = os.path.join(_HERE, '..', 'clubos_domain')


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name + '_scope_test', os.path.join(_DOMAIN, name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


scope = _load('commerce_scope')
gc = _load('gift_card')
af = _load('affiliate')
ce = _load('campaign_engine')


class NormalizeTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(scope.normalize('  Camp-Order '), 'camp_order')
        self.assertEqual(scope.normalize('GEAR_ORDER'), 'gear_order')
        self.assertEqual(scope.normalize(None), '')
        self.assertEqual(scope.normalize(''), '')

    def test_bad_input_never_raises(self):
        self.assertEqual(scope.normalize({'a': 1}).startswith('{'), True)


class GearRejectionTests(unittest.TestCase):
    def test_all_gear_types_rejected(self):
        for t in scope.PLATFORM_GEAR_ORDER_TYPES:
            self.assertTrue(scope.is_gear_order_type(t), t)
            self.assertFalse(scope.is_allowed_order_type(t), t)
            self.assertEqual(scope.check_order_type(t)['reason'],
                             'out-of-scope-gear', t)

    def test_alias_and_case_bypass_blocked(self):
        for t in ('GearOrder', 'gear-order', ' gear_checkout ', 'my_mall_order',
                  'MEDUSA_ORDER', 'equipment_order', 'shop_order_item',
                  'platform_order_v2'):
            self.assertFalse(scope.check_order_type(t)['ok'], t)
            self.assertEqual(scope.check_order_type(t)['reason'],
                             'out-of-scope-gear', t)

    def test_club_types_allowed(self):
        for t in scope.CLUB_ORDER_TYPES:
            r = scope.check_order_type(t)
            self.assertTrue(r['ok'], (t, r))
            self.assertEqual(r['reason'], 'ok')

    def test_uppercase_club_type_normalized(self):
        self.assertTrue(scope.is_allowed_order_type('REGISTRATION'))
        self.assertTrue(scope.is_allowed_order_type('Camp-Order'))

    def test_unknown_type_rejected(self):
        r = scope.check_order_type('invoice')
        self.assertFalse(r['ok'])
        self.assertEqual(r['reason'], 'unknown-order-type')

    def test_missing_type_rejected(self):
        for v in ('', None, 0):
            self.assertEqual(scope.check_order_type(v)['reason'],
                             'missing-order-type')

    def test_note_is_explicit(self):
        self.assertIn('装备', scope.SCOPE_NOTE)


class ConsistencyTests(unittest.TestCase):
    def test_three_whitelists_match(self):
        """domain 单文件加载不互相 import，靠常量复制；这里锁死不许漂移。"""
        self.assertEqual(tuple(scope.CLUB_ORDER_TYPES), tuple(gc.ORDER_TYPES))
        self.assertEqual(tuple(scope.CLUB_ORDER_TYPES), tuple(af.ORDER_TYPES))

    def test_no_gear_anywhere_in_whitelists(self):
        for t in list(gc.ORDER_TYPES) + list(af.ORDER_TYPES):
            self.assertFalse(scope.is_gear_order_type(t), t)

    def test_campaign_first_order_probes_exclude_gear(self):
        probes = ' '.join(scope.FIRST_ORDER_PROBES).lower()
        self.assertIn('registrations', probes)
        self.assertIn('camp_orders', probes)
        self.assertNotIn('gear_orders', probes)
        self.assertNotIn('member_orders', probes)

    def test_engine_probes_match_scope_constant(self):
        """反射拿 _is_first_order 的 co_consts，证明真实代码不再查装备表。"""
        sqls = [x for x in ce.CampaignEngine._is_first_order.__code__.co_consts
                if isinstance(x, str) and 'SELECT 1 FROM' in x]
        self.assertEqual(len(sqls), 2)
        self.assertEqual(tuple(sqls), tuple(scope.FIRST_ORDER_PROBES))
        joined = ' '.join(sqls).lower()
        self.assertNotIn('gear_orders', joined)
        self.assertNotIn('member_orders', joined)

    def test_scope_summary(self):
        d = scope.scope_summary()
        self.assertFalse(d['gearCheckoutWired'])
        self.assertEqual(d['firstOrderTables'], ['registrations', 'camp_orders'])
        self.assertIn('gear_order', d['blockedOrderTypes'])


if __name__ == '__main__':
    unittest.main()
