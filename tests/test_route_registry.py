"""route_registry 路由表单测：注册表完整性 / 校验 / 覆盖归一化 / 生效联动 / 脱敏 / 审计。"""
import importlib.util, os, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'route_registry.py')
    spec = importlib.util.spec_from_file_location('route_registry_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rr = _load()


class TestRegistry(unittest.TestCase):
    def test_keys_unique(self):
        keys = [r['key'] for r in rr.ROUTES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_scopes_and_groups_valid(self):
        groups = {g['key']: g['scope'] for g in rr.GROUPS}
        for r in rr.ROUTES:
            self.assertIn(r['scope'], rr.SCOPES, r['key'])
            self.assertEqual(groups.get(r['group']), r['scope'], r['key'])
            self.assertTrue(r['path'], r['key'])
            self.assertIn(r['kind'], ('view', 'anchor', 'api', 'page', 'deep'))
            self.assertIn(r['auth'], ('public', 'login', 'club', 'platform'))

    def test_navigable_only_web(self):
        for r in rr.ROUTES:
            if r['navigable']:
                self.assertEqual(r['scope'], 'web', r['key'])
                self.assertIn(r['kind'], ('view', 'anchor'))

    def test_registry_matches_lookup(self):
        self.assertEqual(len(rr.resolve('web.home') and rr.ROUTE_BY_KEY), len(rr.ROUTES))
        self.assertIsNone(rr.resolve('nope'))

    def test_registry_view_scope(self):
        v = rr.registry_view('web')
        self.assertTrue(all(x['scope'] == 'web' for x in v['routes']))
        self.assertTrue(all(g['scope'] == 'web' for g in v['groups']))
        self.assertEqual(v['summary']['total'], len(v['routes']))

    def test_registry_view_navigable_only(self):
        v = rr.registry_view('web', navigable_only=True)
        self.assertTrue(all(x['navigable'] for x in v['routes']))
        self.assertGreater(len(v['routes']), 5)
        self.assertLess(v['summary']['total'], len(rr.keys_for_scope('web')))

    def test_registry_view_bad_scope(self):
        with self.assertRaises(ValueError):
            rr.registry_view('hacker')

    def test_all_scopes_have_routes(self):
        for s in rr.SCOPES:
            self.assertGreater(len(rr.keys_for_scope(s)), 0, s)

    def test_public_view_keys_real(self):
        self.assertIn('wme', rr.PUBLIC_VIEW_KEYS)
        self.assertIn('wcamp', rr.PUBLIC_VIEW_KEYS)


class TestPathFor(unittest.TestCase):
    def test_replace_club_id(self):
        self.assertEqual(rr.path_for('web.home', 7), '/web?club_id=7')

    def test_view_hash_kept(self):
        self.assertEqual(rr.path_for('web.activities', 3),
                         '/web?club_id=3#view=wactivities')

    def test_deep_with_id(self):
        self.assertEqual(rr.path_for('web.activityDetail', 2, id=99),
                         '/web?club_id=2&activity=99')

    def test_deep_without_id_returns_template(self):
        p = rr.path_for('web.activityDetail', 2)
        self.assertIn('{id}', p)

    def test_unknown_key(self):
        self.assertEqual(rr.path_for('nope', 1), '')

    def test_club_page_path(self):
        self.assertTrue(rr.path_for('club.routes', 1).startswith('/static/club/'))


class TestOverrideValidation(unittest.TestCase):
    def test_hidden_bool(self):
        self.assertTrue(rr.normalize_override({'key': 'web.home', 'hidden': True}, 'web')['hidden'])
        self.assertFalse(rr.normalize_override({'key': 'web.home', 'hidden': 'false'}, 'web')['hidden'])

    def test_hidden_bad(self):
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'web.home', 'hidden': 'maybe'}, 'web')

    def test_sort_ok(self):
        self.assertEqual(rr.normalize_override({'key': 'web.home', 'sort': 5}, 'web')['sort'], 5)

    def test_sort_out_of_range_rejected(self):
        # 不用夹逼：越界直接拒绝
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'web.home', 'sort': 10000}, 'web')
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'web.home', 'sort': -1}, 'web')

    def test_sort_not_int(self):
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'web.home', 'sort': 'abc'}, 'web')

    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'nope', 'hidden': True}, 'web')

    def test_wrong_scope(self):
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'club.routes', 'hidden': True}, 'web')

    def test_empty_change(self):
        with self.assertRaises(ValueError):
            rr.normalize_override({'key': 'web.home'}, 'web')

    def test_custom_url_ok(self):
        for u in ('/web?x=1', 'https://example.com/a'):
            self.assertEqual(rr.normalize_override({'key': 'web.home', 'customUrl': u}, 'web')['customUrl'], u)

    def test_custom_url_dangerous(self):
        for u in ('javascript:alert(1)', 'data:text/html,x', '//evil.com/x', 'ftp://x'):
            with self.assertRaises(ValueError, msg=u):
                rr.normalize_override({'key': 'web.home', 'customUrl': u}, 'web')

    def test_label_icon_trimmed(self):
        o = rr.normalize_override({'key': 'web.home', 'label': '  首页 ' + 'x' * 100,
                                   'icon': 'Y' * 50}, 'web')
        self.assertLessEqual(len(o['label']), rr.LABEL_MAX)
        self.assertLessEqual(len(o['icon']), rr.ICON_MAX)

    def test_changes_merge_dedupe(self):
        out = rr.normalize_changes({'scope': 'web', 'changes': [
            {'key': 'web.home', 'sort': 1},
            {'key': 'web.home', 'hidden': True},
            {'key': 'web.mall', 'label': '商城'},
        ]}, 'web')
        self.assertEqual(len(out), 2)
        home = [x for x in out if x['key'] == 'web.home'][0]
        self.assertEqual(home['sort'], 1)
        self.assertTrue(home['hidden'])

    def test_changes_bad_payload(self):
        for p in ({}, {'changes': []}, {'changes': 'x'}, None):
            with self.assertRaises(ValueError):
                rr.normalize_changes(p, 'web')

    def test_changes_over_limit(self):
        with self.assertRaises(ValueError):
            rr.normalize_changes({'changes': [{'key': 'web.home', 'sort': 1}] * 101}, 'web')


class TestOverridesFromRows(unittest.TestCase):
    def test_three_state_hidden(self):
        rows = [
            {'route_key': 'web.home', 'hidden': None, 'sort_order': None,
             'label': None, 'icon': None, 'custom_url': None},
            {'route_key': 'web.mall', 'hidden': 0, 'sort_order': 3,
             'label': '装备', 'icon': None, 'custom_url': None},
        ]
        o = rr.overrides_from_rows(rows)
        self.assertNotIn('hidden', o['web.home'])       # NULL=未覆盖
        self.assertFalse(o['web.mall']['hidden'])       # 0=显式显示
        self.assertEqual(o['web.mall']['label'], '装备')

    def test_bad_rows_dropped(self):
        o = rr.overrides_from_rows([{'route_key': 'nope', 'hidden': 1, 'sort_order': None},
                                    None, 'garbage'])
        self.assertEqual(o, {})


class TestEffective(unittest.TestCase):
    def setUp(self):
        self.eff = rr.effective(1, 'web')

    def test_basic_shape(self):
        self.assertEqual(self.eff['scope'], 'web')
        self.assertEqual(self.eff['summary']['total'], len(self.eff['items']))
        self.assertEqual(len(self.eff['enabled']), self.eff['summary']['enabled'])

    def test_camp_hidden_by_default(self):
        camp = [i for i in self.eff['items'] if i['key'] == 'web.camp'][0]
        self.assertFalse(camp['enabled'])
        self.assertEqual(camp['reason'], 'hidden')
        self.assertFalse(camp['visibleByDefault'])

    def test_home_visible(self):
        home = [i for i in self.eff['items'] if i['key'] == 'web.home'][0]
        self.assertTrue(home['enabled'])

    def test_tab_switch_disables(self):
        eff = rr.effective(1, 'web', tab_switches={'activity': False, 'home': True,
                                                   'camp': True, 'bizIntro': True})
        act = [i for i in eff['items'] if i['key'] == 'web.activities'][0]
        self.assertFalse(act['enabled'])
        self.assertEqual(act['reason'], 'tab-off')
        self.assertEqual(eff['summary']['disabledByTab'], 1)

    def test_tab_missing_key_does_not_disable(self):
        eff = rr.effective(1, 'web', tab_switches={'home': True})
        act = [i for i in eff['items'] if i['key'] == 'web.activities'][0]
        self.assertTrue(act['enabled'])

    def test_module_switch_disables(self):
        eff = rr.effective(1, 'web', module_enabled=lambda k: False)
        sv = [i for i in eff['items'] if i['key'] == 'web.storedValue'][0]
        self.assertFalse(sv['enabled'])
        self.assertEqual(sv['reason'], 'module-off')
        # 动态计算，避免每新增一个带 module 联动的 web 项就要改断言
        expected = len([r for r in rr.ROUTES if r['scope'] == 'web' and r.get('module')])
        self.assertEqual(eff['summary']['disabledByModule'], expected)

    def test_module_callback_exception_does_not_disable(self):
        def boom(_):
            raise RuntimeError('db down')
        eff = rr.effective(1, 'web', module_enabled=boom)
        sv = [i for i in eff['items'] if i['key'] == 'web.storedValue'][0]
        self.assertTrue(sv['enabled'])

    def test_hidden_override(self):
        eff = rr.effective(1, 'web', overrides={'web.home': {'key': 'web.home', 'hidden': True}})
        home = [i for i in eff['items'] if i['key'] == 'web.home'][0]
        self.assertFalse(home['enabled'])
        self.assertEqual(home['reason'], 'hidden')

    def test_sort_only_override_keeps_camp_hidden(self):
        # 回归：只改排序不能把默认隐藏的营地放出来
        eff = rr.effective(1, 'web', overrides={'web.camp': {'key': 'web.camp', 'sort': 1}})
        camp = [i for i in eff['items'] if i['key'] == 'web.camp'][0]
        self.assertFalse(camp['enabled'])
        self.assertEqual(camp['order'], 1)

    def test_explicit_show_camp(self):
        eff = rr.effective(1, 'web', overrides={'web.camp': {'key': 'web.camp', 'hidden': False}})
        camp = [i for i in eff['items'] if i['key'] == 'web.camp'][0]
        self.assertTrue(camp['enabled'])

    def test_sort_override_reorders(self):
        eff = rr.effective(1, 'web', overrides={'web.home': {'key': 'web.home', 'sort': 999}})
        self.assertNotEqual(eff['items'][0]['key'], 'web.home')

    def test_label_icon_custom_url(self):
        eff = rr.effective(1, 'web', overrides={'web.home': {
            'key': 'web.home', 'label': '俱乐部首页', 'icon': '🌲',
            'customUrl': '/web?club_id=1&v=2'}})
        home = [i for i in eff['items'] if i['key'] == 'web.home'][0]
        self.assertEqual(home['label'], '俱乐部首页')
        self.assertEqual(home['icon'], '🌲')
        self.assertEqual(home['url'], '/web?club_id=1&v=2')
        self.assertTrue(home['customized'])

    def test_url_map_excludes_deep_templates(self):
        self.assertNotIn('web.activityDetail', self.eff['urlMap'])
        self.assertIn('web.home', self.eff['urlMap'])

    def test_public_only_filters_auth(self):
        eff = rr.effective(1, 'web', public_only=True)
        self.assertTrue(all(i['auth'] == 'public' for i in eff['items']))
        keys = {i['key'] for i in eff['items']}
        self.assertNotIn('web.inbox', keys)
        self.assertNotIn('web.me', keys)

    def test_navigable_only(self):
        eff = rr.effective(1, 'web', navigable_only=True)
        self.assertTrue(all(i['navigable'] for i in eff['items']))
        keys = {i['key'] for i in eff['items']}
        self.assertNotIn('web.storedValue', keys)

    def test_bad_scope(self):
        with self.assertRaises(ValueError):
            rr.effective(1, 'nope')

    def test_club_scope_works(self):
        eff = rr.effective(1, 'club')
        self.assertGreater(eff['summary']['total'], 10)
        self.assertIn('club.routes', eff['urlMap'])


class TestViews(unittest.TestCase):
    def test_public_view_is_sanitized(self):
        v = rr.public_view(rr.effective(1, 'web', public_only=True))
        self.assertEqual(v['scope'], 'web')
        self.assertTrue(v['items'])
        for i in v['items']:
            self.assertEqual(set(i.keys()),
                             {'key', 'label', 'group', 'kind', 'icon', 'order', 'url'})
            self.assertTrue(i['url'])

    def test_public_view_count(self):
        eff = rr.effective(1, 'web', public_only=True)
        v = rr.public_view(eff)
        self.assertEqual(v['count'], len(v['items']))

    def test_audit_view(self):
        rows = [{'id': 1, 'scope': 'web', 'route_key': 'web.home', 'action': 'SET',
                 'detail_json': '{"hidden":1}', 'operator': 'boss', 'created_at': 'x'},
                None, 'bad']
        a = rr.audit_view(rows)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]['detail'], {'hidden': 1})

    def test_loads_bad_json(self):
        self.assertEqual(rr.loads('{bad'), {})
        self.assertEqual(rr.loads(None), {})


class TestPageLayoutWiring(unittest.TestCase):
    """P1-5 接线回归：装修器 targetKey 透传 + render_public 可选 urlMap。"""
    @classmethod
    def setUpClass(cls):
        p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'page_layout.py')
        spec = importlib.util.spec_from_file_location('page_layout_wiring_mod', p)
        cls.pl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pl)

    def _schema(self, key='web.home'):
        return self.pl.normalize_layout({
            'page_key': 'club_home', 'status': 'PUBLISHED',
            'components': [{'type': 'nav_grid', 'props': {'items': [
                {'label': '首页', 'icon': '🏠', 'targetType': 'route', 'targetKey': key}
            ]}}],
        })['components']

    def test_route_target_type_accepted(self):
        self.assertIn('route', self.pl.TARGET_TYPES)

    def test_target_key_kept(self):
        comps = self._schema()
        self.assertEqual(comps[0]['props']['items'][0]['targetKey'], 'web.home')

    def test_no_target_key_keeps_old_shape(self):
        comps = self.pl.normalize_layout({
            'page_key': 'club_home',
            'components': [{'type': 'nav_grid', 'props': {'items': [
                {'label': '活动', 'icon': '⛰️', 'targetType': 'activity', 'targetId': 3}
            ]}}],
        })['components']
        self.assertNotIn('targetKey', comps[0]['props']['items'][0])

    def test_dangerous_key_dropped(self):
        comps = self._schema('javascript:alert(1)')
        self.assertNotIn('targetKey', comps[0]['props']['items'][0])

    def test_render_public_without_map_unchanged(self):
        out = self.pl.render_public(1, self._schema())
        self.assertNotIn('targetUrl', out[0]['props']['items'][0])

    def test_render_public_with_map(self):
        out = self.pl.render_public(1, self._schema(), route_urls={'web.home': '/web?club_id=1'})
        self.assertEqual(out[0]['props']['items'][0]['targetUrl'], '/web?club_id=1')

    def test_disabled_key_has_empty_url(self):
        out = self.pl.render_public(1, self._schema(), route_urls={})
        self.assertEqual(out[0]['props']['items'][0]['targetUrl'], '')

    def test_carousel_also_wired(self):
        comps = self.pl.normalize_layout({
            'page_key': 'club_home',
            'components': [{'type': 'carousel', 'props': {'items': [
                {'image': '/static/uploads/a.jpg', 'targetType': 'route', 'targetKey': 'web.mall'}
            ]}}],
        })['components']
        out = self.pl.render_public(1, comps, route_urls={'web.mall': '/web?club_id=1#view=wmall'})
        self.assertEqual(out[0]['props']['items'][0]['targetUrl'],
                         '/web?club_id=1#view=wmall')


class TestTriStateReversible(unittest.TestCase):
    """回归：hidden 三态可逆（清除覆盖必须真正回落默认，不能被 bool(None) 洗成显示）。"""
    def test_none_clears_override(self):
        o = rr.normalize_override({'key': 'web.camp', 'hidden': None}, 'web')
        self.assertIsNone(o['hidden'])

    def test_none_falls_back_to_default_hidden(self):
        eff = rr.effective(1, 'web', overrides={
            'web.camp': {'key': 'web.camp', 'hidden': None}})
        camp = [i for i in eff['items'] if i['key'] == 'web.camp'][0]
        self.assertFalse(camp['enabled'])
        self.assertEqual(camp['reason'], 'hidden')
        self.assertFalse(camp['hiddenOverridden'])

    def test_explicit_hidden_is_flagged(self):
        eff = rr.effective(1, 'web', overrides={
            'web.home': {'key': 'web.home', 'hidden': True}})
        home = [i for i in eff['items'] if i['key'] == 'web.home'][0]
        self.assertTrue(home['hiddenOverridden'])
        self.assertTrue(home['hidden'])

    def test_no_override_not_flagged(self):
        eff = rr.effective(1, 'web')
        home = [i for i in eff['items'] if i['key'] == 'web.home'][0]
        self.assertFalse(home['hiddenOverridden'])


if __name__ == '__main__':
    unittest.main()
