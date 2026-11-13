"""notifications 通知通道抽象 + 模板单元测试：变量渲染、通道/模板归一化、dispatch 分发路由、站内信落库。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'notifications.py')
    spec = importlib.util.spec_from_file_location('notif_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


nf = _load()


def _memory_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE notification_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, type TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0, config_json TEXT, created_at TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE notification_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'inapp', title TEXT, body TEXT, enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE inapp_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, club_id INTEGER NOT NULL, code TEXT,
        title TEXT, body TEXT, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT)""")
    c.execute("""CREATE TABLE club_integrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, type TEXT, config_json TEXT)""")
    return c


class TestRenderTemplate(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(nf.render_template('你好 {{name}}', {'name': '小野'}), '你好 小野')

    def test_multi(self):
        self.assertEqual(nf.render_template('{{a}}-{{b}}', {'a': 1, 'b': 2}), '1-2')

    def test_missing_var_kept(self):
        self.assertEqual(nf.render_template('{{x}} 还在', {}), '{{x}} 还在')

    def test_empty_body(self):
        self.assertEqual(nf.render_template('', {'a': 1}), '')


class TestNormalizeChannel(unittest.TestCase):
    def test_valid(self):
        n = nf.normalize_channel({'type': 'wechat', 'enabled': True, 'config_json': {'webhook': 'h'}})
        self.assertEqual(n['type'], 'wechat')
        self.assertTrue(n['enabled'])
        self.assertEqual(n['config_json']['webhook'], 'h')

    def test_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            nf.normalize_channel({'type': 'pigeon'})

    def test_default_config(self):
        n = nf.normalize_channel({'type': 'inapp'})
        self.assertEqual(n['config_json'], {})

    def test_merge_current(self):
        cur = {'type': 'sms', 'enabled': True, 'config_json': {'k': 'v'}}
        n = nf.normalize_channel({'enabled': False}, cur)
        self.assertEqual(n['type'], 'sms')  # 沿用 current
        self.assertFalse(n['enabled'])


class TestNormalizeTemplate(unittest.TestCase):
    def test_required_code(self):
        with self.assertRaises(ValueError):
            nf.normalize_template({'body': 'x'})

    def test_default_channel(self):
        n = nf.normalize_template({'code': 'signup'})
        self.assertEqual(n['channel'], 'inapp')
        self.assertTrue(n['enabled'])

    def test_invalid_channel_falls_back(self):
        n = nf.normalize_template({'code': 'x', 'channel': 'pigeon'})
        self.assertEqual(n['channel'], 'inapp')

    def test_truncation(self):
        n = nf.normalize_template({'code': 'x', 'title': 't' * 300, 'body': 'b' * 2000})
        self.assertEqual(len(n['title']), nf.TITLE_MAX)
        self.assertEqual(len(n['body']), nf.BODY_MAX)


class TestJson(unittest.TestCase):
    def test_roundtrip(self):
        d = {'a': 1, 'b': [1, 2]}
        self.assertEqual(nf.loads(nf.dumps(d)), d)
        self.assertEqual(nf.loads(''), {})
        self.assertEqual(nf.loads('bad'), {})


class TestDispatch(unittest.TestCase):
    def test_inapp_writes(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'inapp', '欢迎 {{name}}', '报名成功 {{act}}', 1))
        res = nf.dispatch(c, club_id=1, code='signup', ctx={'name': '小野', 'act': '雪山徒步'}, user_id=42)
        self.assertTrue(res['sent'])
        self.assertEqual(res['channel'], 'inapp')
        row = c.execute('SELECT title,body,user_id,is_read FROM inapp_messages').fetchone()
        self.assertEqual(row['title'], '欢迎 小野')
        self.assertEqual(row['body'], '报名成功 雪山徒步')
        self.assertEqual(row['user_id'], 42)
        self.assertEqual(row['is_read'], 0)

    def test_no_template_skipped(self):
        c = _memory_db()
        res = nf.dispatch(c, club_id=1, code='nope')
        self.assertFalse(res['sent'])
        self.assertEqual(res['reason'], 'no-template')

    def test_disabled_template_skipped(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'inapp', 't', 'b', 0))
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertFalse(res['sent'])
        self.assertEqual(res['reason'], 'no-template')

    def test_channel_disabled(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'wechat', 't', 'b', 1))
        # 未配置 wechat 通道 → 跳过
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertFalse(res['sent'])
        self.assertEqual(res['reason'], 'channel-disabled')

    def test_platform_fallback(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (0, 'signup', 'inapp', '平台欢迎', 'b', 1))
        res = nf.dispatch(c, club_id=1, code='signup', user_id=7)
        self.assertTrue(res['sent'])
        row = c.execute('SELECT title FROM inapp_messages').fetchone()
        self.assertEqual(row['title'], '平台欢迎')

    def test_wechat_no_webhook(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'wechat', 't', 'b', 1))
        c.execute("INSERT INTO notification_channels(club_id,type,enabled,config_json) VALUES(?,?,1,?)",
                  (1, 'wechat', nf.dumps({})))
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertEqual(res['reason'], 'wechat-no-webhook')

    def test_wechat_with_webhook(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'wechat', '报名 {{act}}', 'b', 1))
        c.execute("INSERT INTO notification_channels(club_id,type,enabled,config_json) VALUES(?,?,1,?)",
                  (1, 'wechat', nf.dumps({})))
        c.execute("INSERT INTO club_integrations(club_id,type,config_json) VALUES(?,?,?)",
                  (1, 'wechat', nf.dumps({'webhook': 'http://127.0.0.1:9/nope'})))
        # 网络不可达但 dispatch 必须吞掉异常、不抛错
        res = nf.dispatch(c, club_id=1, code='signup', ctx={'act': '雪山'})
        self.assertFalse(res['sent'])
        self.assertIn(res['reason'], ('wechat-error', 'wechat-no-webhook'))


class TestHubPolicy(unittest.TestCase):
    """v0.40：设置中枢的外发总开关 / 免打扰时段，只约束外发通道，且完全可选。"""

    def _wechat_db(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'wechat', 't', 'b', 1))
        c.execute("INSERT INTO notification_channels(club_id,type,enabled,config_json) VALUES(?,?,1,?)",
                  (1, 'wechat', nf.dumps({})))
        return c

    def test_no_hub_backward_compatible(self):
        c = self._wechat_db()
        self.assertEqual(nf.dispatch(c, club_id=1, code='signup')['reason'], 'wechat-no-webhook')

    def test_outbound_disabled(self):
        c = self._wechat_db()
        res = nf.dispatch(c, club_id=1, code='signup',
                          hub={'outboundEnabled': False, 'quietHours': (22, 8), 'hour': 12})
        self.assertEqual(res['reason'], 'hub-outbound-disabled')
        self.assertFalse(res['sent'])

    def test_quiet_hours(self):
        c = self._wechat_db()
        res = nf.dispatch(c, club_id=1, code='signup',
                          hub={'outboundEnabled': True, 'quietHours': (22, 8), 'hour': 23})
        self.assertEqual(res['reason'], 'hub-quiet-hours')

    def test_outside_quiet_hours(self):
        c = self._wechat_db()
        res = nf.dispatch(c, club_id=1, code='signup',
                          hub={'outboundEnabled': True, 'quietHours': (22, 8), 'hour': 12})
        self.assertEqual(res['reason'], 'wechat-no-webhook')

    def test_inapp_not_blocked(self):
        c = _memory_db()
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'inapp', 't', 'b', 1))
        res = nf.dispatch(c, club_id=1, code='signup', user_id=7,
                          hub={'outboundEnabled': False, 'quietHours': (22, 8), 'hour': 23})
        self.assertTrue(res['sent'])
        self.assertEqual(res['reason'], 'inapp-queued')

    def test_hub_broken_does_not_break(self):
        c = self._wechat_db()
        res = nf.dispatch(c, club_id=1, code='signup', hub='not-a-dict')
        self.assertIn(res['reason'], ('wechat-no-webhook', 'exception'))


class TestRealIntegrationSchema(unittest.TestCase):
    """回归：真实库 club_integrations 只有 wecom_webhook/wecom_enabled，没有 type/config_json。

    旧实现直接查不存在的列 → OperationalError 被吞成 'exception'，企微通道从未真正发出。
    """

    def _legacy_db(self, webhook='', enabled=0):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE notification_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER, type TEXT,
            enabled INTEGER, config_json TEXT)""")
        c.execute("""CREATE TABLE notification_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER, code TEXT,
            channel TEXT, title TEXT, body TEXT, enabled INTEGER)""")
        c.execute("""CREATE TABLE inapp_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, club_id INTEGER,
            code TEXT, title TEXT, body TEXT, is_read INTEGER, created_at TEXT)""")
        c.execute("""CREATE TABLE club_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER,
            wecom_webhook TEXT, wecom_enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT)""")
        c.execute("INSERT INTO notification_templates(club_id,code,channel,title,body,enabled) VALUES(?,?,?,?,?,?)",
                  (1, 'signup', 'wechat', 't', 'b', 1))
        c.execute("INSERT INTO notification_channels(club_id,type,enabled,config_json) VALUES(?,?,1,?)",
                  (1, 'wechat', nf.dumps({})))
        c.execute("INSERT INTO club_integrations(club_id,wecom_webhook,wecom_enabled) VALUES(?,?,?)",
                  (1, webhook, enabled))
        return c

    def test_missing_columns_no_exception(self):
        c = self._legacy_db()
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertEqual(res['reason'], 'wechat-no-webhook')

    def test_legacy_webhook_used(self):
        c = self._legacy_db(webhook='http://127.0.0.1:9/nope', enabled=1)
        res = nf.dispatch(c, club_id=1, code='signup')
        # 网络不可达 → wechat-error（证明真的读到了 webhook 并尝试发送，不是 exception）
        self.assertEqual(res['reason'], 'wechat-error')

    def test_legacy_disabled_not_used(self):
        c = self._legacy_db(webhook='http://127.0.0.1:9/nope', enabled=0)
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertEqual(res['reason'], 'wechat-no-webhook')

    def test_channel_config_webhook_takes_priority(self):
        c = self._legacy_db()
        c.execute("UPDATE notification_channels SET config_json=? WHERE club_id=1",
                  (nf.dumps({'webhook': 'http://127.0.0.1:9/nope'}),))
        res = nf.dispatch(c, club_id=1, code='signup')
        self.assertEqual(res['reason'], 'wechat-error')


if __name__ == '__main__':
    unittest.main()
