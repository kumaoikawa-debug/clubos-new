"""miniprogram_bridge 小程序对接桥接层单测：路径白名单与防开放重定向、
配置/绑定归一与三态、DAO 幂等、开关联动、bootstrap 脱敏、就绪度评分、异常安全。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'miniprogram_bridge.py')
    spec = importlib.util.spec_from_file_location('mp_bridge_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mb = _load()
NOW = 1763000000


def _memory_db(with_mp=True):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE miniprogram_bridge_config (
        club_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
        config_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT, updated_by TEXT)""")
    c.execute("""CREATE TABLE miniprogram_bridge_bindings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        capability_key TEXT NOT NULL, page_path TEXT, enabled INTEGER NOT NULL DEFAULT 1,
        hidden INTEGER, sort_order INTEGER NOT NULL DEFAULT 0, note TEXT,
        updated_at TEXT, updated_by TEXT, UNIQUE(club_id, capability_key))""")
    c.execute("""CREATE TABLE miniprogram_bridge_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        ready INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0,
        level TEXT, missing_json TEXT, detail_json TEXT, operator TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE miniprogram_bridge_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        action TEXT NOT NULL, detail_json TEXT, operator TEXT, created_at TEXT)""")
    if with_mp:
        c.execute("""CREATE TABLE club_miniprogram (
            club_id INTEGER PRIMARY KEY, appid TEXT, app_name TEXT,
            authorized INTEGER NOT NULL DEFAULT 0, current_version TEXT,
            release_status TEXT, privacy_status TEXT)""")
    # 个人态聚合所需既有表（缺表回落也要能测）
    c.execute('CREATE TABLE stored_value_accounts (club_id INTEGER, user_id INTEGER, balance_cents INTEGER)')
    c.execute('CREATE TABLE gift_card_accounts (club_id INTEGER, user_id INTEGER, balance_cents INTEGER)')
    c.execute('CREATE TABLE club_members (club_id INTEGER, user_id INTEGER, club_points_balance INTEGER)')
    c.execute('CREATE TABLE affiliate_members (club_id INTEGER, user_id INTEGER, code TEXT, status TEXT)')
    return c


def _setup_mp(c, club=1, appid='wx1234567890', authorized=1, version='1.2.0'):
    c.execute('INSERT OR REPLACE INTO club_miniprogram(club_id,appid,app_name,authorized,current_version) '
              'VALUES(?,?,?,?,?)', (club, appid, '远拓户外', authorized, version))


class TestPathSafety(unittest.TestCase):
    def test_native_paths_accepted(self):
        for p in ('pages/index/index', 'pages/member/wallet?tab=1',
                  'subpackages/gift/index?id={id}', 'pages/a-b_c/d.e'):
            self.assertEqual(mb.normalize_page_path(p), p, p)

    def test_empty_clears_binding(self):
        self.assertEqual(mb.normalize_page_path(None), '')
        self.assertEqual(mb.normalize_page_path(''), '')
        self.assertEqual(mb.normalize_page_path('   '), '')

    def test_external_urls_rejected(self):
        for p in ('https://evil.com/pages/x', 'http://a.cn', '//evil.com/x',
                  'javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd',
                  'pages/x\\..\\y', 'ftp://x/pages/a'):
            self.assertIsNone(mb.normalize_page_path(p), p)

    def test_whitespace_traversal_and_shape_rejected(self):
        for p in ('pages/a b', 'pages/../../etc', '/pages/index/index',
                  'api/pages/x', 'pages/a<script>', 'p' * 200):
            self.assertIsNone(mb.normalize_page_path(p), p)

    def test_fallback_whitelist(self):
        self.assertTrue(mb.is_safe_fallback('/web?club_id=1'))
        self.assertTrue(mb.is_safe_fallback('/api/public/clubs/1/x'))
        self.assertTrue(mb.is_safe_fallback('/static/club/stores.html'))
        self.assertTrue(mb.is_safe_fallback('/api/club/1/notify'))
        self.assertFalse(mb.is_safe_fallback('https://x/api/public/a'))
        self.assertFalse(mb.is_safe_fallback('/api/private/secret'))
        self.assertFalse(mb.is_safe_fallback('/web?club_id=1 evil'))

    def test_substitute_only_club_id(self):
        self.assertEqual(mb.substitute('/web?club_id={club_id}', 7), '/web?club_id=7')
        self.assertEqual(mb.substitute('/api/public/activities/{activity_id}/price-quote', 7),
                         '/api/public/activities/{activity_id}/price-quote')

    def test_all_registered_fallbacks_are_real_and_safe(self):
        for cap in mb.CAPABILITIES:
            self.assertTrue(mb.is_safe_fallback(cap['fallback']), cap['key'])
            self.assertTrue(cap['api'].startswith(('/api/', '/static/')), cap['key'])
        # 带 {club_id} 的模板必须能在服务端替换；不带的是真实固定端点（如 wechat/login）
        self.assertEqual(mb.substitute('/web?club_id={club_id}', 3), '/web?club_id=3')
        self.assertEqual(mb.substitute('/api/public/wechat/login', 3),
                         '/api/public/wechat/login')

    def test_capability_registry_integrity(self):
        keys = [x['key'] for x in mb.CAPABILITIES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(x['audience'] in mb.CAP_AUDIENCES for x in mb.CAPABILITIES))
        self.assertEqual(mb.TOTAL_WEIGHT, sum(x['weight'] for x in mb.CHECKS))


class TestConfigAndBinding(unittest.TestCase):
    def test_default_config(self):
        d = mb.default_config()
        self.assertTrue(d['enabled'])
        self.assertEqual(d['launchMode'], 'h5')
        self.assertEqual(d['defaultTab'], 'home')

    def test_partial_update_keeps_other_fields(self):
        cfg = mb.normalize_config({'enabled': True, 'launchMode': 'native', 'notice': '欢迎'},
                                  mb.default_config())
        cfg2 = mb.normalize_config({'notice': '新版'}, cfg)
        self.assertEqual(cfg2['launchMode'], 'native')
        self.assertEqual(cfg2['notice'], '新版')

    def test_bad_values_dropped(self):
        cfg = mb.normalize_config({'launchMode': 'evil', 'defaultTab': '<script>',
                                   'maxPerRow': 999, 'unknown': 1}, mb.default_config())
        self.assertEqual(cfg['launchMode'], 'h5')
        self.assertEqual(cfg['defaultTab'], 'home')
        self.assertEqual(cfg['maxPerRow'], 4)
        self.assertNotIn('unknown', cfg)

    def test_binding_hidden_tristate(self):
        b0 = mb.normalize_binding({})
        self.assertIsNone(b0['hidden'])
        b1 = mb.normalize_binding({'hidden': True})
        self.assertIs(b1['hidden'], True)
        b2 = mb.normalize_binding({'hidden': None}, b1)
        self.assertIsNone(b2['hidden'])

    def test_binding_illegal_path_returns_none(self):
        self.assertIsNone(mb.normalize_binding({'pagePath': 'https://evil.com'}))
        self.assertEqual(mb.normalize_binding({'pagePath': ''})['page_path'], '')

    def test_binding_out_of_range_sort_dropped(self):
        # 三铁律：越界值是坏输入 → 静默丢弃回落默认，不夹逼成上限
        self.assertEqual(mb.normalize_binding({'sort': 99999999})['sort_order'], 0)
        self.assertEqual(mb.normalize_binding({'sort': 7})['sort_order'], 7)


class TestDao(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()

    def test_config_save_load_upsert_isolation(self):
        self.assertEqual(mb.load_config(self.c, 1)['launchMode'], 'h5')
        mb.save_config(self.c, 1, {'enabled': False, 'launchMode': 'native'}, 'admin')
        cfg = mb.load_config(self.c, 1)
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['launchMode'], 'native')
        mb.save_config(self.c, 1, {'launchMode': 'h5'}, 'admin')
        self.assertEqual(mb.load_config(self.c, 1)['launchMode'], 'h5')
        self.assertTrue(mb.load_config(self.c, 2)['enabled'])

    def test_binding_save_load_reset(self):
        b = mb.normalize_binding({'pagePath': 'pages/gift/index', 'sort': 5, 'note': '礼包页'})
        self.assertTrue(mb.save_binding(self.c, 1, 'giftCard', b, 'admin'))
        binds = mb.load_bindings(self.c, 1)
        self.assertIn('giftCard', binds)
        self.assertEqual(binds['giftCard']['page_path'], 'pages/gift/index')
        self.assertEqual(mb.load_bindings(self.c, 2), {})
        mb.save_binding(self.c, 1, 'giftCard', mb.normalize_binding({'sort': 9}, binds['giftCard']))
        self.assertEqual(mb.load_bindings(self.c, 1)['giftCard']['sort_order'], 9)
        self.assertTrue(mb.reset_binding(self.c, 1, 'giftCard'))
        self.assertFalse(mb.reset_binding(self.c, 1, 'giftCard'))
        self.assertEqual(mb.load_bindings(self.c, 1), {})

    def test_unknown_capability_not_saved(self):
        self.assertFalse(mb.save_binding(self.c, 1, 'notExist', mb.normalize_binding({})))

    def test_corrupt_config_json_falls_back(self):
        self.c.execute('INSERT INTO miniprogram_bridge_config(club_id,enabled,config_json) VALUES(1,1,?)',
                       ('{broken',))
        self.assertEqual(mb.load_config(self.c, 1)['defaultTab'], 'home')

    def test_missing_tables_do_not_raise(self):
        c = _memory_db(with_mp=False)
        c.execute('DROP TABLE miniprogram_bridge_config')
        c.execute('DROP TABLE miniprogram_bridge_bindings')
        self.assertTrue(mb.load_config(c, 1)['enabled'])
        self.assertEqual(mb.load_bindings(c, 1), {})
        eff = mb.effective(c, 1)
        self.assertGreater(eff['summary']['total'], 0)

    def test_audit_and_check_roundtrip(self):
        self.assertTrue(mb.audit(self.c, 1, 'binding.update', {'key': 'giftCard'}, 'admin'))
        rows = mb.list_audit(self.c, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['detail']['key'], 'giftCard')
        r = mb.check_readiness(self.c, 1, secret_configured=True, now=NOW)
        self.assertTrue(mb.record_check(self.c, 1, r, 'admin'))
        checks = mb.list_checks(self.c, 1)
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['score'], r['score'])
        self.assertEqual(len(checks[0]['checks']), len(mb.CHECKS))


class TestEffective(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()

    def test_default_all_web_enabled_and_h5_fallback(self):
        eff = mb.effective(self.c, 1)
        self.assertEqual(eff['summary']['total'], len(mb.WEB_KEYS))
        self.assertEqual(eff['summary']['enabled'], len(mb.WEB_KEYS))
        self.assertFalse(any(i['audience'] == 'club' for i in eff['items']))
        self.assertTrue(all(i['pathKind'] in ('h5', 'api') for i in eff['items']))
        self.assertFalse(any(i['nativeBound'] for i in eff['items']))

    def test_include_club_adds_admin_items(self):
        eff = mb.effective(self.c, 1, include_club=True)
        self.assertEqual(eff['summary']['total'], len(mb.CAPABILITIES))
        self.assertTrue(any(i['key'] == 'notify' for i in eff['items']))

    def test_module_switch_disables_linked_capabilities(self):
        off = lambda k: k != 'giftCard'
        eff = mb.effective(self.c, 1, module_enabled=off)
        gift = next(i for i in eff['items'] if i['key'] == 'giftCard')
        self.assertFalse(gift['enabled'])
        self.assertEqual(gift['reason'], 'module-off')
        self.assertTrue(next(i for i in eff['items'] if i['key'] == 'home')['enabled'])
        self.assertGreaterEqual(eff['summary']['disabledByModule'], 1)

    def test_bridge_off_disables_all(self):
        mb.save_config(self.c, 1, {'enabled': False})
        eff = mb.effective(self.c, 1)
        self.assertEqual(eff['summary']['enabled'], 0)
        self.assertTrue(all(i['reason'] == 'bridge-off' for i in eff['items']))

    def test_binding_off_and_hidden(self):
        mb.save_binding(self.c, 1, 'giftCard', mb.normalize_binding({'enabled': False}))
        eff = mb.effective(self.c, 1)
        self.assertEqual(next(i for i in eff['items'] if i['key'] == 'giftCard')['reason'],
                         'binding-off')
        mb.save_binding(self.c, 1, 'giftCard', mb.normalize_binding({'hidden': True}))
        eff = mb.effective(self.c, 1)
        self.assertEqual(next(i for i in eff['items'] if i['key'] == 'giftCard')['reason'], 'hidden')

    def test_callback_exception_falls_back_to_enabled(self):
        def boom(k):
            raise RuntimeError('db down')
        eff = mb.effective(self.c, 1, module_enabled=boom)
        self.assertEqual(eff['summary']['enabled'], len(mb.WEB_KEYS))

    def test_native_binding_only_used_in_native_mode(self):
        mb.save_binding(self.c, 1, 'giftCard', mb.normalize_binding({'pagePath': 'pages/gift/index'}))
        eff = mb.effective(self.c, 1)
        g = next(i for i in eff['items'] if i['key'] == 'giftCard')
        self.assertFalse(g['nativeBound'])
        self.assertEqual(g['pathKind'], 'api')
        mb.save_config(self.c, 1, {'launchMode': 'native'})
        eff = mb.effective(self.c, 1)
        g = next(i for i in eff['items'] if i['key'] == 'giftCard')
        self.assertTrue(g['nativeBound'])
        self.assertEqual(g['path'], 'pages/gift/index')
        self.assertEqual(g['pathKind'], 'native')

    def test_custom_sort_changes_order(self):
        mb.save_binding(self.c, 1, 'wechatLogin', mb.normalize_binding({'sort': 1}))
        eff = mb.effective(self.c, 1)
        self.assertEqual(eff['items'][0]['key'], 'wechatLogin')


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        _setup_mp(self.c)

    def test_bootstrap_public_shape_and_desensitization(self):
        b = mb.bootstrap(self.c, 1, mp_info={'appid': 'wx1', 'appName': '远拓',
                                             'secret': 'MUST_NOT_LEAK', 'subjectName': '公司'},
                         mp_settings={'siteName': '远拓', 'primaryColor': '#f60', 'evil': object()},
                         now=NOW)
        self.assertNotIn('secret', b['app'])
        self.assertNotIn('subjectName', b['app'])
        self.assertEqual(b['app']['appid'], 'wx1')
        self.assertNotIn('evil', b['settings'])
        self.assertEqual(b['launchMode'], 'h5')
        self.assertEqual(b['generatedAt'], mb._ts_to_str(NOW))
        self.assertFalse(b['loggedIn'])
        self.assertEqual(b['summary']['delivered'], b['summary']['enabled'])

    def test_login_required_caps_locked_when_anonymous(self):
        b = mb.bootstrap(self.c, 1)
        locked = [x for x in b['capabilities'] if x['locked']]
        self.assertTrue(locked)
        self.assertTrue(all(x['requiresLogin'] for x in locked))
        b2 = mb.bootstrap(self.c, 1, user_id=9)
        self.assertTrue(b2['loggedIn'])
        self.assertFalse(any(x['locked'] for x in b2['capabilities']))

    def test_navigation_excludes_login_caps_when_anonymous(self):
        # navigation 仍下发 login 项但带 requiresLogin 标记，由小程序自行决定置灰
        b = mb.bootstrap(self.c, 1)
        self.assertEqual(len(b['navigation']), len(mb.WEB_KEYS))
        self.assertTrue(any(x['requiresLogin'] for x in b['navigation']))

    def test_disabled_caps_hidden_unless_show_disabled(self):
        mb.save_config(self.c, 1, {'enabled': False})
        b = mb.bootstrap(self.c, 1)
        self.assertFalse(b['enabled'])
        self.assertEqual(b['capabilities'], [])
        cfg = mb.load_config(self.c, 1)
        cfg['showDisabled'] = True
        mb.save_config(self.c, 1, cfg)
        b2 = mb.bootstrap(self.c, 1)
        self.assertEqual(len(b2['capabilities']), len(mb.WEB_KEYS))
        self.assertTrue(all(not x['enabled'] for x in b2['capabilities']))

    def test_capabilities_view_no_admin_items(self):
        eff = mb.effective(self.c, 1, include_club=True)
        caps = mb.capabilities_view(eff, user_id=1)
        self.assertEqual(len(caps), len(mb.WEB_KEYS))

    def test_my_view_balances_are_separate(self):
        self.c.execute('INSERT INTO stored_value_accounts VALUES(1,9,12345)')
        self.c.execute('INSERT INTO gift_card_accounts VALUES(1,9,6789)')
        self.c.execute('INSERT INTO club_members VALUES(1,9,300)')
        self.c.execute("INSERT INTO affiliate_members VALUES(1,9,'CODE9','ACTIVE')")
        v = mb.my_view(self.c, 1, 9)
        self.assertEqual(v['amountUnit'], 'cent')
        self.assertEqual(v['balances']['storedValueCents'], 12345)
        self.assertEqual(v['balances']['giftCardCents'], 6789)
        self.assertEqual(v['balances']['clubPoints'], 300)
        self.assertEqual(v['affiliate']['code'], 'CODE9')

    def test_my_view_missing_tables_and_bad_user(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        v = mb.my_view(c, 1, 9)
        self.assertEqual(v['balances']['giftCardCents'], 0)
        self.assertEqual(mb.my_view(self.c, 1, 0)['balances'], {})


class TestReadiness(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()

    def test_blocked_when_nothing_configured(self):
        r = mb.check_readiness(self.c, 1, now=NOW)
        self.assertFalse(r['ready'])
        self.assertEqual(r['level'], 'BLOCKED')
        self.assertGreaterEqual(len(r['missing']), 3)
        self.assertIn('小程序 appid 已登记', r['missing'])

    def test_required_items_gate_ready(self):
        _setup_mp(self.c)
        r = mb.check_readiness(self.c, 1, secret_configured=False, now=NOW)
        self.assertFalse(r['ready'])
        self.assertIn('登录密钥已配置（环境变量）', r['missing'])
        r2 = mb.check_readiness(self.c, 1, secret_configured=True, now=NOW)
        self.assertTrue(r2['ready'])
        self.assertEqual(r2['level'], 'READY')
        self.assertEqual(r2['score'], 100)

    def test_bridge_off_blocks(self):
        _setup_mp(self.c)
        mb.save_config(self.c, 1, {'enabled': False})
        r = mb.check_readiness(self.c, 1, secret_configured=True)
        self.assertFalse(r['ready'])
        self.assertIn('桥接总开关已开启', r['missing'])

    def test_native_mode_requires_binding(self):
        _setup_mp(self.c, version='')
        mb.save_config(self.c, 1, {'launchMode': 'native'})
        r = mb.check_readiness(self.c, 1, secret_configured=True)
        native = next(x for x in r['checks'] if x['key'] == 'nativePaths')
        self.assertFalse(native['ok'])
        mb.save_binding(self.c, 1, 'giftCard', mb.normalize_binding({'pagePath': 'pages/gift/index'}))
        r2 = mb.check_readiness(self.c, 1, secret_configured=True)
        self.assertTrue(next(x for x in r2['checks'] if x['key'] == 'nativePaths')['ok'])

    def test_h5_mode_native_check_auto_ok(self):
        r = mb.check_readiness(self.c, 1)
        self.assertTrue(next(x for x in r['checks'] if x['key'] == 'nativePaths')['ok'])

    def test_module_callback_exception_does_not_crash(self):
        def boom(k):
            raise RuntimeError
        r = mb.check_readiness(self.c, 1, module_enabled=boom)
        self.assertEqual(len(r['checks']), len(mb.CHECKS))

    def test_score_between_zero_and_hundred(self):
        for secret in (False, True):
            r = mb.check_readiness(self.c, 1, secret_configured=secret)
            self.assertTrue(0 <= r['score'] <= 100)

    def test_platform_overview(self):
        rows = [
            {'club_id': 1, 'name': 'A', 'ready': 1, 'score': 100, 'level': 'READY', 'created_at': 'x'},
            {'club_id': 2, 'name': 'B', 'ready': 0, 'score': 40, 'level': 'BLOCKED', 'created_at': 'y'},
            {'club_id': 3, 'name': 'C', 'ready': 0, 'score': 0, 'level': 'UNCHECKED', 'created_at': ''},
        ]
        o = mb.platform_overview(self.c, rows)
        self.assertEqual(o['summary']['total'], 3)
        self.assertEqual(o['summary']['checked'], 2)
        self.assertEqual(o['summary']['ready'], 1)
        self.assertEqual(o['summary']['avgScore'], 70)


if __name__ == '__main__':
    unittest.main()
