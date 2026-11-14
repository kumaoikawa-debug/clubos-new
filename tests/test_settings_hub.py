"""settings_hub 设置中枢单测：注册表 / 校验 / 三级回落 / 只读聚合 / 覆盖与重置 / 审计 / 策略读取。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'settings_hub.py')
    spec = importlib.util.spec_from_file_location('settings_hub_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sh = _load()


def _memory_db(with_module_tables=True):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE settings_hub_values (
        club_id INTEGER NOT NULL, key TEXT NOT NULL, value_json TEXT, updated_at TEXT,
        UNIQUE(club_id, key))""")
    c.execute("""CREATE TABLE settings_hub_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, key TEXT NOT NULL,
        old_value_json TEXT, new_value_json TEXT, action TEXT, operator TEXT, note TEXT,
        created_at TEXT)""")
    if with_module_tables:
        c.execute("CREATE TABLE clubs (id INTEGER PRIMARY KEY, name TEXT, c_profile_json TEXT)")
        c.execute("CREATE TABLE club_mp_settings (club_id INTEGER, settings_json TEXT)")
        c.execute("CREATE TABLE page_layouts (club_id INTEGER, status TEXT)")
        # REGISTRY 的 campaigns probe 用 owner_type/owner_club_id（P1-4 修正后的真实表结构）；
        # 旧内存表只建 club_id，导致这条 SQL 恒抛异常、count 恒为 None。
        c.execute("CREATE TABLE marketing_campaigns (owner_type TEXT, owner_club_id INTEGER, status TEXT)")
        c.execute("CREATE TABLE stored_value_config (club_id INTEGER, enabled INTEGER)")
        c.execute("CREATE TABLE group_buy_campaigns (club_id INTEGER, status TEXT)")
        c.execute("CREATE TABLE engagement_configs (club_id INTEGER, kind TEXT, enabled INTEGER)")
        c.execute("CREATE TABLE notification_channels (club_id INTEGER, enabled INTEGER)")
        c.execute("CREATE TABLE notification_templates (club_id INTEGER, enabled INTEGER)")
        c.execute("CREATE TABLE inapp_messages (club_id INTEGER)")
        c.execute("CREATE TABLE stores (club_id INTEGER, status TEXT)")
        c.execute("CREATE TABLE club_members (club_id INTEGER)")
        c.execute("INSERT INTO clubs(id,name,c_profile_json) VALUES(1,'远拓','{}')")
    return c


class TestQuietHours(unittest.TestCase):
    def test_cross_midnight(self):
        self.assertTrue(sh.in_quiet_hours(23, 22, 8))
        self.assertTrue(sh.in_quiet_hours(2, 22, 8))
        self.assertTrue(sh.in_quiet_hours(22, 22, 8))
        self.assertFalse(sh.in_quiet_hours(8, 22, 8))
        self.assertFalse(sh.in_quiet_hours(12, 22, 8))

    def test_same_value_disabled(self):
        for h in range(24):
            self.assertFalse(sh.in_quiet_hours(h, 9, 9))

    def test_daytime_range(self):
        self.assertTrue(sh.in_quiet_hours(13, 12, 18))
        self.assertFalse(sh.in_quiet_hours(11, 12, 18))
        self.assertFalse(sh.in_quiet_hours(18, 12, 18))

    def test_bad_input(self):
        self.assertFalse(sh.in_quiet_hours('x', None, 8))


class TestRegistry(unittest.TestCase):
    def test_registry_shape(self):
        self.assertEqual(len(sh.REGISTRY), len(sh.MODULE_BY_KEY))
        self.assertEqual(len(sh.PREFERENCES), len(sh.PREF_BY_KEY))
        for m in sh.REGISTRY:
            self.assertIn(m['group'], [g['key'] for g in sh.GROUPS])
            self.assertTrue(m['probe']['sql'].count('?') == 1,
                            'probe SQL 必须只带一个 club_id 占位符: %s' % m['key'])
            self.assertIn(m['probe']['mode'], ('count', 'scalar'))

    def test_registry_view_has_no_sql(self):
        v = sh.registry_view()
        raw = sh.dumps(v)
        self.assertNotIn('SELECT', raw)
        self.assertNotIn('probe', raw)
        self.assertEqual(set(v['toggleableModules']), set(sh.TOGGLEABLE))

    def test_toggleable_whitelist(self):
        for k in sh.TOGGLEABLE:
            self.assertIsNotNone(sh.spec_for('module.' + k))
        for m in sh.REGISTRY:
            if not m['toggleable']:
                self.assertIsNone(sh.spec_for('module.' + m['key']),
                                  '未接线模块不能提供假开关: %s' % m['key'])

    def test_unknown_key(self):
        self.assertIsNone(sh.spec_for('module.notExist'))
        self.assertIsNone(sh.spec_for('foo.bar'))


class TestNormalize(unittest.TestCase):
    def test_bool(self):
        spec = sh.spec_for('notify.outboundEnabled')
        for v in (True, 'true', '1', 'ON', 1):
            self.assertIs(sh.normalize_value(spec, v), True)
        for v in (False, 'false', '0', 'off', 0):
            self.assertIs(sh.normalize_value(spec, v), False)

    def test_bool_reject(self):
        spec = sh.spec_for('notify.outboundEnabled')
        for v in ('maybe', 2, None, [], {}):
            with self.assertRaises(ValueError):
                sh.normalize_value(spec, v)

    def test_int_bounds_reject_not_clamp(self):
        spec = sh.spec_for('notify.quietHoursStart')
        self.assertEqual(sh.normalize_value(spec, 23), 23)
        self.assertEqual(sh.normalize_value(spec, '7'), 7)
        for v in (-1, 24, 999, 'abc'):
            with self.assertRaises(ValueError):
                sh.normalize_value(spec, v)

    def test_enum(self):
        spec = sh.spec_for('portal.homeLayoutMode')
        self.assertEqual(sh.normalize_value(spec, 'default'), 'default')
        with self.assertRaises(ValueError):
            sh.normalize_value(spec, 'fancy')

    def test_changes_nested_and_flat(self):
        a = sh.normalize_changes({'changes': {'notify.outboundEnabled': False,
                                              'notify.quietHoursStart': 21}})
        b = sh.normalize_changes({'notify.outboundEnabled': False,
                                  'notify.quietHoursStart': 21})
        self.assertEqual(a, b)
        self.assertEqual(a['notify.quietHoursStart'], 21)

    def test_changes_reject_unknown(self):
        with self.assertRaises(ValueError):
            sh.normalize_changes({'module.campaigns': False})
        with self.assertRaises(ValueError):
            sh.normalize_changes({'anything': 1})
        with self.assertRaises(ValueError):
            sh.normalize_changes({})
        with self.assertRaises(ValueError):
            sh.normalize_changes('not-a-dict')

    def test_note_key_ignored(self):
        self.assertEqual(sh.normalize_changes({'note': 'x', 'notify.outboundEnabled': True}),
                         {'notify.outboundEnabled': True})


class TestFallback(unittest.TestCase):
    def test_default(self):
        c = _memory_db()
        g = sh.get(c, 1, 'notify.quietHoursStart')
        self.assertEqual(g['value'], 22)
        self.assertEqual(g['scope'], 'default')
        self.assertFalse(g['overridden'])

    def test_platform_then_club(self):
        c = _memory_db()
        c.execute("INSERT INTO settings_hub_values VALUES(0,?,?,?)",
                  ('notify.quietHoursStart', sh.dumps(20), 't'))
        g = sh.get(c, 1, 'notify.quietHoursStart')
        self.assertEqual(g['value'], 20)
        self.assertEqual(g['scope'], 'platform')
        c.execute("INSERT INTO settings_hub_values VALUES(1,?,?,?)",
                  ('notify.quietHoursStart', sh.dumps(21), 't'))
        g = sh.get(c, 1, 'notify.quietHoursStart')
        self.assertEqual(g['value'], 21)
        self.assertEqual(g['scope'], 'club')

    def test_broken_value_falls_back(self):
        c = _memory_db()
        c.execute("INSERT INTO settings_hub_values VALUES(1,?,?,?)",
                  ('notify.outboundEnabled', '{broken', 't'))
        self.assertIs(sh.get(c, 1, 'notify.outboundEnabled')['value'], True)

    def test_missing_table_falls_back(self):
        c = sqlite3.connect(':memory:')
        self.assertEqual(sh.get(c, 1, 'notify.quietHoursStart')['value'], 22)
        self.assertTrue(sh.is_module_enabled(c, 1, 'pageLayout'))

    def test_module_toggle(self):
        c = _memory_db()
        self.assertTrue(sh.is_module_enabled(c, 1, 'pageLayout'))
        sh.set_many(c, 1, {'module.pageLayout': False})
        self.assertFalse(sh.is_module_enabled(c, 1, 'pageLayout'))
        self.assertEqual(sh.get(c, 1, 'module.pageLayout')['scope'], 'club')


class TestSnapshot(unittest.TestCase):
    def _seed(self, c):
        c.execute("INSERT INTO page_layouts VALUES(1,'PUBLISHED')")
        c.execute("INSERT INTO page_layouts VALUES(1,'DRAFT')")
        c.execute("INSERT INTO stored_value_config VALUES(1,1)")
        c.execute("INSERT INTO marketing_campaigns VALUES('CLUB',1,'ACTIVE')")
        c.execute("INSERT INTO marketing_campaigns VALUES('CLUB',1,'DRAFT')")
        c.execute("INSERT INTO engagement_configs VALUES(1,'CHECKIN',1)")
        c.execute("INSERT INTO stores VALUES(1,'ACTIVE')")
        c.execute("INSERT INTO notification_channels VALUES(1,1)")
        c.execute("INSERT INTO club_members VALUES(1)")

    def test_shape_and_counts(self):
        c = _memory_db()
        self._seed(c)
        s = sh.snapshot(c, 1)
        by = {m['key']: m for m in s['modules']}
        self.assertEqual(len(s['modules']), len(sh.REGISTRY))
        self.assertEqual(len(s['groups']), len(sh.GROUPS))
        self.assertEqual(by['pageLayout']['count'], 1)
        self.assertTrue(by['pageLayout']['configured'])
        self.assertTrue(by['storedValue']['configured'])
        self.assertEqual(by['campaigns']['count'], 1)
        self.assertFalse(by['groupBuy']['configured'])
        self.assertEqual(s['summary']['totalModules'], len(sh.REGISTRY))
        self.assertEqual(s['summary']['disabledByHub'], 0)

    def test_hub_off_marks_effective(self):
        c = _memory_db()
        self._seed(c)
        sh.set_many(c, 1, {'module.pageLayout': False})
        s = sh.snapshot(c, 1)
        by = {m['key']: m for m in s['modules']}
        self.assertFalse(by['pageLayout']['hubEnabled'])
        self.assertFalse(by['pageLayout']['effectiveEnabled'])
        self.assertTrue(by['storedValue']['effectiveEnabled'])
        self.assertEqual(s['summary']['disabledByHub'], 1)

    def test_missing_tables_are_unknown_not_false(self):
        c = _memory_db(with_module_tables=False)
        s = sh.snapshot(c, 1)
        unknown = [m for m in s['modules'] if m['configured'] is None]
        self.assertEqual(len(unknown), len(sh.REGISTRY))
        self.assertEqual(s['summary']['unknownModules'], len(sh.REGISTRY))
        self.assertEqual(s['summary']['configuredModules'], 0)

    def test_preferences_view(self):
        c = _memory_db()
        sh.set_many(c, 0, {'portal.homeLayoutMode': 'default'})
        s = sh.snapshot(c, 1)
        by = {p['key']: p for p in s['preferences']}
        self.assertEqual(by['portal.homeLayoutMode']['value'], 'default')
        self.assertEqual(by['portal.homeLayoutMode']['scope'], 'platform')
        self.assertTrue(by['portal.homeLayoutMode']['overridden'])
        self.assertEqual(by['notify.quietHoursEnd']['value'], 8)
        # BUG-HUB-009：平台默认不算「本俱乐部覆盖」，俱乐部自己写一条才算。
        self.assertEqual(s['summary']['overriddenPreferences'], 0)
        sh.set_many(c, 1, {'notify.quietHoursStart': 21})
        self.assertEqual(sh.snapshot(c, 1)['summary']['overriddenPreferences'], 1)
        self.assertEqual(sh.snapshot(c, 0)['summary']['overriddenPreferences'], 1)


class TestSetResetAudit(unittest.TestCase):
    def test_set_and_audit(self):
        c = _memory_db()
        r = sh.set_many(c, 1, {'notify.outboundEnabled': False,
                               'module.stamp': False}, operator='admin', note='夜间维护')
        self.assertEqual(set(r['updated']), {'notify.outboundEnabled', 'module.stamp'})
        a = sh.audit_list(c, 1)
        self.assertEqual(len(a), 2)
        self.assertEqual(a[0]['operator'], 'admin')
        self.assertEqual(a[0]['note'], '夜间维护')
        self.assertEqual(a[0]['action'], 'SET')

    def test_same_value_skipped_no_audit(self):
        c = _memory_db()
        sh.set_many(c, 1, {'notify.outboundEnabled': False})
        r = sh.set_many(c, 1, {'notify.outboundEnabled': False})
        self.assertEqual(r['skipped'], ['notify.outboundEnabled'])
        self.assertEqual(len(sh.audit_list(c, 1)), 1)

    def test_invalid_key_raises(self):
        c = _memory_db()
        with self.assertRaises(ValueError):
            sh.set_many(c, 1, {'module.campaigns': False})
        with self.assertRaises(ValueError):
            sh.set_many(c, 1, {'notify.quietHoursStart': 99})
        with self.assertRaises(ValueError):
            sh.set_many(c, 1, {})

    def test_reset_single(self):
        c = _memory_db()
        sh.set_many(c, 0, {'notify.quietHoursStart': 20})
        sh.set_many(c, 1, {'notify.quietHoursStart': 21})
        sh.reset(c, 1, ['notify.quietHoursStart'])
        g = sh.get(c, 1, 'notify.quietHoursStart')
        self.assertEqual(g['value'], 20)
        self.assertEqual(g['scope'], 'platform')
        acts = [a['action'] for a in sh.audit_list(c, 1)]
        self.assertIn('RESET', acts)

    def test_reset_all(self):
        c = _memory_db()
        sh.set_many(c, 1, {'module.pageLayout': False, 'notify.outboundEnabled': False})
        r = sh.reset(c, 1, None, operator='admin2')
        self.assertEqual(set(r['reset']), {'module.pageLayout', 'notify.outboundEnabled'})
        resets = [a for a in sh.audit_list(c, 1) if a['action'] == 'RESET']
        self.assertTrue(resets and all(a['operator'] == 'admin2' for a in resets))
        self.assertTrue(sh.is_module_enabled(c, 1, 'pageLayout'))
        self.assertIs(sh.get(c, 1, 'notify.outboundEnabled')['value'], True)

    def test_reset_invalid(self):
        c = _memory_db()
        with self.assertRaises(ValueError):
            sh.reset(c, 1, ['module.campaigns'])

    def test_audit_decode_and_limit(self):
        c = _memory_db()
        for i in range(5):
            sh.set_many(c, 1, {'notify.quietHoursStart': i})
        a = sh.audit_list(c, 1, limit=3)
        self.assertEqual(len(a), 3)
        self.assertEqual(a[0]['newValue'], 4)
        self.assertEqual(a[0]['oldValue'], 3)

    def test_audit_missing_table(self):
        c = sqlite3.connect(':memory:')
        self.assertEqual(sh.audit_list(c, 1), [])


class TestPolicies(unittest.TestCase):
    def test_portal_defaults(self):
        c = _memory_db()
        p = sh.portal_policy(c, 1)
        self.assertEqual(p['homeLayoutMode'], 'decorated')
        self.assertTrue(p['decoratorEnabled'])
        self.assertTrue(p['storedValueEnabled'])
        self.assertTrue(all(p['engagementEnabled'].values()))

    def test_portal_overrides(self):
        c = _memory_db()
        sh.set_many(c, 1, {'portal.homeLayoutMode': 'default',
                           'module.pageLayout': False,
                           'module.checkin': False})
        p = sh.portal_policy(c, 1)
        self.assertEqual(p['homeLayoutMode'], 'default')
        self.assertFalse(p['decoratorEnabled'])
        self.assertFalse(p['engagementEnabled']['checkin'])
        self.assertTrue(p['engagementEnabled']['lottery'])

    def test_notify_policy(self):
        c = _memory_db()
        p = sh.notify_policy(c, 1)
        self.assertTrue(p['outboundEnabled'])
        self.assertEqual(p['quietHours'], (22, 8))
        self.assertIn(p['hour'], range(24))
        sh.set_many(c, 1, {'notify.outboundEnabled': False,
                           'notify.quietHoursStart': 23})
        p = sh.notify_policy(c, 1)
        self.assertFalse(p['outboundEnabled'])
        self.assertEqual(p['quietHours'], (23, 8))

    def test_policies_on_broken_db(self):
        c = sqlite3.connect(':memory:')
        self.assertTrue(sh.notify_policy(c, 1)['outboundEnabled'])
        self.assertTrue(sh.portal_policy(c, 1)['decoratorEnabled'])
        self.assertEqual(sh.portal_policy(c, 1)['homeLayoutMode'], 'decorated')


class TestJsonHelpers(unittest.TestCase):
    def test_loads_tolerant(self):
        self.assertEqual(sh.loads(None), {})
        self.assertEqual(sh.loads('{bad'), {})
        self.assertEqual(sh.loads('{"a":1}'), {'a': 1})
        self.assertEqual(sh.loads(sh.dumps({'中': 1})), {'中': 1})

    def test_decode(self):
        self.assertEqual(sh._decode('false'), (False, True))
        self.assertEqual(sh._decode('22'), (22, True))
        self.assertEqual(sh._decode(None), (None, False))


if __name__ == '__main__':
    unittest.main()
