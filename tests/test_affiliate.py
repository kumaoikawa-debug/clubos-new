"""affiliate 分销员单测：金额/费率、配置合并、绑定防刷、佣金入账幂等、冲回、结算状态机。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'affiliate.py')
    spec = importlib.util.spec_from_file_location('affiliate_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


af = _load()
NOW = 1763000000  # 固定时间，避免测试受真实时钟影响


def _memory_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE affiliate_configs (
        club_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
        config_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT)""")
    c.execute("""CREATE TABLE affiliate_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        code TEXT NOT NULL, level TEXT, rate_bp_override INTEGER,
        status TEXT NOT NULL DEFAULT 'PENDING', note TEXT,
        total_orders INTEGER NOT NULL DEFAULT 0, total_amount_cents INTEGER NOT NULL DEFAULT 0,
        total_commission_cents INTEGER NOT NULL DEFAULT 0, settled_cents INTEGER NOT NULL DEFAULT 0,
        created_at TEXT, UNIQUE(club_id, user_id), UNIQUE(code))""")
    c.execute("""CREATE TABLE affiliate_bindings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        affiliate_user_id INTEGER NOT NULL, invitee_user_id INTEGER NOT NULL,
        code TEXT, channel TEXT NOT NULL DEFAULT 'code', status TEXT NOT NULL DEFAULT 'ACTIVE',
        created_at TEXT, expires_at TEXT, UNIQUE(club_id, invitee_user_id))""")
    c.execute("""CREATE TABLE affiliate_commissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        affiliate_user_id INTEGER NOT NULL, invitee_user_id INTEGER NOT NULL, binding_id INTEGER,
        order_type TEXT NOT NULL, order_id TEXT NOT NULL,
        order_amount_cents INTEGER NOT NULL DEFAULT 0, rate_bp INTEGER NOT NULL DEFAULT 0,
        commission_cents INTEGER NOT NULL DEFAULT 0, level_key TEXT,
        status TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT, available_at TEXT,
        settled_at TEXT, settlement_id TEXT, reversed_at TEXT,
        UNIQUE(club_id, order_type, order_id, affiliate_user_id))""")
    c.execute("""CREATE TABLE affiliate_settlements (
        id TEXT PRIMARY KEY, club_id INTEGER NOT NULL, affiliate_user_id INTEGER NOT NULL,
        gross_cents INTEGER NOT NULL DEFAULT 0, entry_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'GENERATED', payment_ref TEXT, created_at TEXT, paid_at TEXT)""")
    return c


def _member(c, club=1, uid=10, status='ACTIVE', rate=None, code='A12345678',
            total_orders=0, total_amount=0, total_comm=0, settled=0):
    cur = c.execute("""INSERT INTO affiliate_members
        (club_id,user_id,code,rate_bp_override,status,total_orders,total_amount_cents,
         total_commission_cents,settled_cents) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (club, uid, code, rate, status, total_orders, total_amount, total_comm, settled))
    return cur.lastrowid


def _bind(c, club=1, aff=10, invitee=20, expires=None, status='ACTIVE'):
    cur = c.execute("""INSERT INTO affiliate_bindings
        (club_id,affiliate_user_id,invitee_user_id,status,created_at,expires_at)
        VALUES(?,?,?,?,?,?)""", (club, aff, invitee, status, af._ts_to_str(NOW), expires))
    return cur.lastrowid


def _cfg(**kw):
    d = af.default_config()
    d['enabled'] = True
    d['freezeDays'] = 0
    d['minSettleCents'] = 0
    d.update(kw)
    return d


class TestMoneyAndLevels(unittest.TestCase):
    def test_to_cents_and_back(self):
        self.assertEqual(af.to_cents(100), 10000)
        self.assertEqual(af.to_cents(0.01), 1)
        self.assertEqual(af.from_cents(12345), 123.45)
        self.assertEqual(af.to_cents('abc'), 0)
        self.assertEqual(af.to_cents(-1), 0)
        self.assertEqual(af.to_cents(999999999), 0)   # 越界不夹逼成最大值

    def test_compute_commission_integer(self):
        self.assertEqual(af.compute_commission(39900, 500), 1995)      # ¥399 × 5% = ¥19.95
        self.assertEqual(af.compute_commission(1, 500), 0)             # 分 × bp 向下取整
        self.assertEqual(af.compute_commission(10000, 10000), 0)       # 越界费率回落 0

    def test_normalize_levels_drops_invalid(self):
        lv = af.normalize_levels([
            {'key': 'A', 'rateBp': 300, 'minAmountCents': 0, 'minOrders': 0},
            {'key': '', 'rateBp': 500},           # 无 key → 丢
            {'key': 'B', 'rateBp': 0},            # 0 费率 → 丢
            {'key': 'C', 'rateBp': 99999},        # 超上限 → 丢（不夹逼）
            'garbage',
        ])
        self.assertEqual(len(lv), 1)
        self.assertEqual(lv[0]['key'], 'A')

    def test_config_patch_keeps_amount_unit(self):
        """P1-1 教训回归：只改 enabled 时不能把「分」当「元」再换算一次。"""
        c1 = af.normalize_config({'enabled': True, 'minSettleAmount': 10,
                                  'defaultRateBp': 500, 'freezeDays': 7})
        self.assertEqual(c1['minSettleCents'], 1000)
        c2 = af.normalize_config({'enabled': False}, c1)
        self.assertEqual(c2['minSettleCents'], 1000)
        self.assertEqual(c2['defaultRateBp'], 500)
        self.assertEqual(c2['freezeDays'], 7)

    def test_config_from_row_and_public(self):
        cfg = af.normalize_config({'enabled': True, 'defaultRateBp': 800})
        row = {'club_id': 1, 'enabled': 1, 'config_json': af.dumps(cfg)}
        back = af.config_from_row(row)
        self.assertEqual(back['defaultRateBp'], 800)
        self.assertTrue(back['enabled'])
        pub = af.public_config(back)
        self.assertEqual(set(pub), {'enabled', 'autoApprove'})   # 不下发费率/档位

    def test_config_from_broken_row_falls_back(self):
        self.assertEqual(af.config_from_row({'config_json': '{bad'})['defaultRateBp'], 500)

    def test_resolve_level_highest_match(self):
        cfg = _cfg()
        m = {'total_amount_cents': 120000, 'total_orders': 6, 'rate_bp_override': None}
        self.assertEqual(af.resolve_level(cfg, m)['key'], 'L2')
        m2 = {'total_amount_cents': 600000, 'total_orders': 25, 'rate_bp_override': None}
        self.assertEqual(af.resolve_level(cfg, m2)['key'], 'L3')

    def test_rate_override_wins(self):
        cfg = _cfg()
        r, key, _l, src = af.effective_rate_bp(cfg, {'rate_bp_override': 1200})
        self.assertEqual((r, src), (1200, 'override'))
        self.assertEqual(key, 'CUSTOM')

    def test_gen_code_charset(self):
        code = af.gen_code('Y', 8)
        self.assertTrue(code.startswith('Y'))
        self.assertEqual(len(code), 9)
        self.assertTrue(all(ch in af.CODE_ALPHABET for ch in code[1:]))


class TestBinding(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        _member(self.c)

    def test_bind_ok(self):
        r = af.bind(self.c, club_id=1, affiliate_user_id=10, invitee_user_id=20, now=NOW)
        self.assertTrue(r['ok'])
        self.assertIsNotNone(r['bindingId'])

    def test_self_bind_rejected(self):
        r = af.bind(self.c, club_id=1, affiliate_user_id=10, invitee_user_id=10, now=NOW)
        self.assertEqual(r['reason'], 'self-bind')

    def test_duplicate_binding_rejected(self):
        _bind(self.c)
        r = af.bind(self.c, club_id=1, affiliate_user_id=10, invitee_user_id=20, now=NOW)
        self.assertEqual(r['reason'], 'already-bound')

    def test_non_active_affiliate_rejected(self):
        self.c.execute("UPDATE affiliate_members SET status='PENDING' WHERE user_id=10")
        r = af.bind(self.c, club_id=1, affiliate_user_id=10, invitee_user_id=20, now=NOW)
        self.assertEqual(r['reason'], 'affiliate-not-active')

    def test_cycle_and_affiliate_invitee_rejected(self):
        _member(self.c, uid=20, status='ACTIVE', code='B12345678')
        # 20 已是分销员 → 不能成为 10 的下线
        r = af.bind(self.c, club_id=1, affiliate_user_id=10, invitee_user_id=20, now=NOW)
        self.assertEqual(r['reason'], 'invitee-is-affiliate')

    def test_active_binding_expired(self):
        _bind(self.c, expires=str(NOW - 10))
        self.assertIsNone(af.active_binding(self.c, club_id=1, invitee_user_id=20, now=NOW))

    def test_active_binding_frozen_member(self):
        _bind(self.c)
        self.c.execute("UPDATE affiliate_members SET status='FROZEN' WHERE user_id=10")
        self.assertIsNone(af.active_binding(self.c, club_id=1, invitee_user_id=20, now=NOW))

    def test_active_binding_club_isolated(self):
        _bind(self.c, club=1)
        self.assertIsNone(af.active_binding(self.c, club_id=2, invitee_user_id=20, now=NOW))


class TestPreviewAndAccrue(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        _member(self.c)
        _bind(self.c)

    def test_preview_readonly(self):
        before = self.c.execute('SELECT COUNT(*) FROM affiliate_commissions').fetchone()[0]
        q = af.preview(self.c, club_id=1, buyer_user_id=20, amount_cents=39900,
                       cfg=_cfg(), now=NOW)
        self.assertTrue(q['eligible'])
        self.assertEqual(q['commissionCents'], 1995)
        self.assertEqual(q['rateBp'], 500)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM affiliate_commissions').fetchone()[0], before)

    def test_preview_reasons(self):
        base = dict(club_id=1, buyer_user_id=20, amount_cents=39900, now=NOW)
        self.assertEqual(af.preview(self.c, cfg=_cfg(enabled=False), **base)['reason'], 'disabled')
        self.assertEqual(af.preview(self.c, cfg=_cfg(), module_enabled=False, **base)['reason'],
                         'module-disabled')
        self.assertEqual(af.preview(self.c, cfg=_cfg(), buyer_user_id=20, amount_cents=0,
                                    club_id=1, now=NOW)['reason'], 'invalid-amount')
        self.assertEqual(af.preview(self.c, cfg=_cfg(), buyer_user_id=999, amount_cents=39900,
                                    club_id=1, now=NOW)['reason'], 'no-binding')

    def test_self_purchase_blocked_by_default(self):
        _bind(self.c, aff=10, invitee=10) if False else None
        # 直接构造：把绑定指向分销员本人（DB 层允许，domain 层拦截）
        self.c.execute('DELETE FROM affiliate_bindings')
        self.c.execute("""INSERT INTO affiliate_bindings
            (club_id,affiliate_user_id,invitee_user_id,status,created_at)
            VALUES(1,10,10,'ACTIVE',?)""", (af._ts_to_str(NOW),))
        q = af.preview(self.c, club_id=1, buyer_user_id=10, amount_cents=39900,
                       cfg=_cfg(), now=NOW)
        self.assertEqual(q['reason'], 'self-purchase')
        q2 = af.preview(self.c, club_id=1, buyer_user_id=10, amount_cents=39900,
                        cfg=_cfg(allowSelfPurchase=True), now=NOW)
        self.assertTrue(q2['eligible'])

    def test_accrue_updates_totals_and_idempotent(self):
        r = af.accrue(self.c, club_id=1, order_type='registration', order_id='R1',
                      buyer_user_id=20, amount_cents=39900, cfg=_cfg(), now=NOW)
        self.assertTrue(r['accrued'])
        self.assertEqual(r['commissionCents'], 1995)
        m = self.c.execute('SELECT * FROM affiliate_members WHERE user_id=10').fetchone()
        self.assertEqual(m['total_orders'], 1)
        self.assertEqual(m['total_amount_cents'], 39900)
        self.assertEqual(m['total_commission_cents'], 1995)
        # 同一订单重复入账 → 幂等，不重复加钱
        r2 = af.accrue(self.c, club_id=1, order_type='registration', order_id='R1',
                       buyer_user_id=20, amount_cents=39900, cfg=_cfg(), now=NOW)
        self.assertFalse(r2['accrued'])
        self.assertEqual(r2['reason'], 'duplicate')
        m2 = self.c.execute('SELECT * FROM affiliate_members WHERE user_id=10').fetchone()
        self.assertEqual(m2['total_orders'], 1)
        self.assertEqual(m2['total_commission_cents'], 1995)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM affiliate_commissions').fetchone()[0], 1)

    def test_accrue_different_order_type_not_idempotent_collision(self):
        kw = dict(c=self.c, club_id=1, buyer_user_id=20, amount_cents=10000, cfg=_cfg(), now=NOW)
        self.assertTrue(af.accrue(order_type='registration', order_id='1', **kw)['accrued'])
        self.assertTrue(af.accrue(order_type='camp_order', order_id='1', **kw)['accrued'])
        # 营销适用范围硬边界：装备订单一律拒绝入账
        self.assertFalse(af.accrue(order_type='gear_order', order_id='1', **kw)['accrued'])

    def test_accrue_invalid_order(self):
        r = af.accrue(self.c, club_id=1, order_type='hack', order_id='X',
                      buyer_user_id=20, amount_cents=10000, cfg=_cfg(), now=NOW)
        self.assertEqual(r['reason'], 'invalid-order')

    def test_accrue_never_raises_on_missing_table(self):
        c = sqlite3.connect(':memory:')
        r = af.accrue(c, club_id=1, order_type='registration', order_id='R',
                      buyer_user_id=20, amount_cents=10000, cfg=_cfg(), now=NOW)
        self.assertFalse(r['accrued'])
        self.assertIn(r['reason'], ('exception', 'no-binding'))


class TestReverseAndSettle(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        _member(self.c)
        _bind(self.c)
        self.kw = dict(c=self.c, club_id=1, buyer_user_id=20, amount_cents=10000,
                       cfg=_cfg(), now=NOW)

    def test_reverse_pending_and_skip_settled(self):
        af.accrue(order_type='registration', order_id='R1', **self.kw)
        af.accrue(order_type='registration', order_id='R2', **self.kw)
        s = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=_cfg(), now=NOW)
        self.assertTrue(s['settled'])
        r = af.reverse(self.c, club_id=1, order_type='registration', order_id='R1', now=NOW)
        self.assertEqual(r['skippedSettled'], 1)
        self.assertEqual(r['reversed'], 0)
        # 新增一条 PENDING 再冲回
        af.accrue(order_type='registration', order_id='R3', **self.kw)
        r2 = af.reverse(self.c, club_id=1, order_type='registration', order_id='R3', now=NOW)
        self.assertEqual(r2['reversed'], 1)
        self.assertEqual(r2['cents'], 500)
        row = self.c.execute("SELECT status FROM affiliate_commissions WHERE order_id='R3'").fetchone()
        self.assertEqual(row['status'], 'REVERSED')

    def test_settle_respects_freeze_period(self):
        cfg = _cfg(freezeDays=7)
        af.accrue(self.c, club_id=1, order_type='registration', order_id='R1',
                  buyer_user_id=20, amount_cents=10000, cfg=cfg, now=NOW)
        early = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=cfg, now=NOW + 3600)
        self.assertEqual(early['reason'], 'nothing-due')
        late = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=cfg,
                         now=NOW + 8 * 86400)
        self.assertTrue(late['settled'])
        self.assertEqual(late['entryCount'], 1)
        self.assertEqual(late['grossCents'], 500)

    def test_settle_below_minimum(self):
        cfg = _cfg(minSettleCents=10000)
        af.accrue(self.c, club_id=1, order_type='registration', order_id='R1',
                  buyer_user_id=20, amount_cents=10000, cfg=cfg, now=NOW)
        r = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=cfg, now=NOW)
        self.assertEqual(r['reason'], 'below-minimum')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM affiliate_settlements').fetchone()[0], 0)

    def test_settle_idempotent_and_marks_paid(self):
        af.accrue(order_type='registration', order_id='R1', **self.kw)
        s = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=_cfg(), now=NOW)
        self.assertTrue(s['settled'])
        sid = s['settlementId']
        again = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=_cfg(), now=NOW)
        self.assertEqual(again['reason'], 'nothing-due')
        p1 = af.mark_settlement_paid(self.c, club_id=1, settlement_id=sid,
                                     payment_ref='BANK-1', now=NOW)
        self.assertTrue(p1['ok'])
        p2 = af.mark_settlement_paid(self.c, club_id=1, settlement_id=sid, now=NOW)
        self.assertEqual(p2['reason'], 'already-paid')
        st = self.c.execute('SELECT * FROM affiliate_settlements WHERE id=?', (sid,)).fetchone()
        self.assertEqual(st['status'], 'PAID')
        self.assertEqual(st['payment_ref'], 'BANK-1')

    def test_settle_frozen_member_rejected(self):
        af.accrue(order_type='registration', order_id='R1', **self.kw)
        self.c.execute("UPDATE affiliate_members SET status='FROZEN' WHERE user_id=10")
        r = af.settle(self.c, club_id=1, affiliate_user_id=10, cfg=_cfg(), now=NOW)
        self.assertEqual(r['reason'], 'member-frozen')

    def test_views(self):
        af.accrue(order_type='registration', order_id='R1', **self.kw)
        row = self.c.execute('SELECT * FROM affiliate_commissions').fetchone()
        v = af.commission_view(row)
        self.assertEqual(v['commission'], 5.0)
        self.assertEqual(v['orderAmount'], 100.0)
        self.assertEqual(v['rateBp'], 500)
        mv = af.member_view(self.c.execute('SELECT * FROM affiliate_members').fetchone(), _cfg())
        self.assertEqual(mv['pendingCents'], 500)


if __name__ == '__main__':
    unittest.main()
