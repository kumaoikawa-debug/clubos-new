"""stored_value 储值账户单测：金额单位换算、档位规则归一化、赠额计算、抵扣试算、记账与幂等。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'stored_value.py')
    spec = importlib.util.spec_from_file_location('stored_value_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sv = _load()


def _memory_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE stored_value_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        balance_cents INTEGER NOT NULL DEFAULT 0, total_recharged_cents INTEGER NOT NULL DEFAULT 0,
        total_bonus_cents INTEGER NOT NULL DEFAULT 0, total_spent_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT, updated_at TEXT,
        UNIQUE(club_id, user_id))""")
    c.execute("""CREATE TABLE stored_value_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        type TEXT NOT NULL, amount_cents INTEGER NOT NULL, balance_after_cents INTEGER NOT NULL,
        source_type TEXT, source_id TEXT, operator TEXT, note TEXT, created_at TEXT)""")
    c.execute("""CREATE UNIQUE INDEX idx_svl_idem
                 ON stored_value_ledger(club_id, user_id, source_type, source_id)
                 WHERE source_id IS NOT NULL""")
    return c


class TestMoney(unittest.TestCase):
    def test_to_cents(self):
        self.assertEqual(sv.to_cents(100), 10000)
        self.assertEqual(sv.to_cents(0.01), 1)
        self.assertEqual(sv.to_cents(19.995), 2000)

    def test_to_cents_invalid(self):
        for v in ('', None, 'abc', -1, 0, float('nan'), float('inf')):
            self.assertEqual(sv.to_cents(v), 0)

    def test_from_cents_roundtrip(self):
        for yuan in (0.01, 19.99, 500, 1234.56):
            self.assertEqual(sv.from_cents(sv.to_cents(yuan)), yuan)


class TestRules(unittest.TestCase):
    def test_normalize_rule(self):
        r = sv.normalize_rule({'minAmount': 500, 'bonusAmount': 50, 'label': '充500送50'})
        self.assertEqual(r['minCents'], 50000)
        self.assertEqual(r['bonusCents'], 5000)
        self.assertEqual(r['minAmount'], 500.0)
        self.assertTrue(r['enabled'])

    def test_zero_bonus_allowed(self):
        r = sv.normalize_rule({'minAmount': 100, 'bonusAmount': 0})
        self.assertEqual(r['bonusCents'], 0)

    def test_bad_rule_dropped(self):
        self.assertIsNone(sv.normalize_rule({'minAmount': 0, 'bonusAmount': 10}))
        self.assertIsNone(sv.normalize_rule({'bonusAmount': 10}))
        self.assertIsNone(sv.normalize_rule('not a dict'))
        self.assertIsNone(sv.normalize_rule({'minAmount': 99999999}))

    def test_normalize_rules_sorted(self):
        raw = [{'minAmount': 100, 'bonusAmount': 5}, {'minAmount': 1000, 'bonusAmount': 150},
               {'minAmount': 500, 'bonusAmount': 50}]
        rules = sv.normalize_rules(raw)
        self.assertEqual([r['minCents'] for r in rules], [100000, 50000, 10000])

    def test_normalize_rules_from_json(self):
        rules = sv.normalize_rules(sv.dumps([{'minAmount': 200, 'bonusAmount': 20}]))
        self.assertEqual(len(rules), 1)
        self.assertEqual(sv.normalize_rules('not json'), [])


class TestConfig(unittest.TestCase):
    def test_default_disabled(self):
        cfg = sv.normalize_config({})
        self.assertFalse(cfg['enabled'])  # 未配置 → 默认关闭，不影响既有链路
        self.assertEqual(cfg['maxPayRatio'], 100)
        self.assertEqual(cfg['rules'], [])

    def test_ratio_clamped(self):
        self.assertEqual(sv.normalize_config({'maxPayRatio': 500})['maxPayRatio'], 100)
        self.assertEqual(sv.normalize_config({'maxPayRatio': -3})['maxPayRatio'], 0)

    def test_merge_current(self):
        cur = {'enabled': True, 'maxPayRatio': 50, 'rules': [{'minCents': 10000, 'bonusCents': 1000,
               'minAmount': 100.0, 'bonusAmount': 10.0, 'label': '', 'enabled': True}]}
        cfg = sv.normalize_config({'maxPayRatio': 80}, cur)
        self.assertTrue(cfg['enabled'])
        self.assertEqual(cfg['maxPayRatio'], 80)
        self.assertEqual(len(cfg['rules']), 1)

    def test_partial_patch_keeps_min_recharge_units(self):
        """★ 回归：只 PATCH enabled 时，最低充值门槛不能被「分→元」放大 100 倍。"""
        cur = {'enabled': True, 'allowActivityPay': True, 'maxPayRatio': 50,
               'minRechargeCents': 10000, 'minRechargeAmount': 100.0, 'rules': []}
        cfg = sv.normalize_config({'enabled': False}, cur)
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['minRechargeCents'], 10000, cfg)
        self.assertEqual(cfg['maxPayRatio'], 50)

    def test_partial_patch_base_cents_only(self):
        """base 只有「分」字段时也要正确换算（兼容旧调用方）。"""
        cfg = sv.normalize_config({}, {'enabled': True, 'minRechargeCents': 5000, 'rules': []})
        self.assertEqual(cfg['minRechargeCents'], 5000, cfg)

    def test_config_from_row(self):
        row = {'enabled': 1, 'allow_activity_pay': 0, 'max_pay_ratio': 60,
               'min_recharge_cents': 10000,
               'rules_json': sv.dumps([{'minAmount': 500, 'bonusAmount': 50}]), 'updated_at': 'x'}
        cfg = sv.config_from_row(row)
        self.assertTrue(cfg['enabled'])
        self.assertFalse(cfg['allowActivityPay'])
        self.assertEqual(cfg['maxPayRatio'], 60)
        self.assertEqual(cfg['minRechargeAmount'], 100.0)
        self.assertEqual(len(cfg['rules']), 1)


class TestBonus(unittest.TestCase):
    def setUp(self):
        self.rules = sv.normalize_rules([
            {'minAmount': 100, 'bonusAmount': 5},
            {'minAmount': 500, 'bonusAmount': 50},
            {'minAmount': 1000, 'bonusAmount': 150},
        ])

    def test_best_tier(self):
        self.assertEqual(sv.compute_bonus(10000, self.rules), 500)      # ¥100 → 送 ¥5
        self.assertEqual(sv.compute_bonus(50000, self.rules), 5000)     # ¥500 → 送 ¥50
        self.assertEqual(sv.compute_bonus(120000, self.rules), 15000)   # ¥1200 → 最高档送 ¥150

    def test_below_min(self):
        self.assertEqual(sv.compute_bonus(9900, self.rules), 0)

    def test_disabled_tier(self):
        rules = [dict(r, enabled=False) for r in self.rules]
        self.assertEqual(sv.compute_bonus(120000, rules), 0)

    def test_invalid(self):
        self.assertEqual(sv.compute_bonus(-1, self.rules), 0)
        self.assertEqual(sv.compute_bonus('x', self.rules), 0)
        self.assertEqual(sv.compute_bonus(10000, None), 0)


class TestQuotePay(unittest.TestCase):
    def test_full_cover(self):
        q = sv.quote_pay(5000, 10000)
        self.assertEqual(q['usableCents'], 5000)
        self.assertEqual(q['remainingCents'], 0)

    def test_partial_cover(self):
        q = sv.quote_pay(10000, 3000)
        self.assertEqual(q['usableCents'], 3000)
        self.assertEqual(q['remainingCents'], 7000)

    def test_ratio_cap(self):
        q = sv.quote_pay(10000, 10000, 50)
        self.assertEqual(q['usableCents'], 5000)

    def test_zero_ratio(self):
        self.assertEqual(sv.quote_pay(10000, 10000, 0)['usableCents'], 0)


class TestApplyLedger(unittest.TestCase):
    def test_recharge_and_bonus(self):
        c = _memory_db()
        r1 = sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=50000, ledger_type='RECHARGE',
                             source_type='recharge', source_id='r1')
        r2 = sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=5000, ledger_type='BONUS',
                             source_type='recharge_bonus', source_id='r1')
        self.assertTrue(r1['ok'])
        self.assertEqual(r1['balanceCents'], 50000)
        self.assertTrue(r2['ok'])
        self.assertEqual(r2['balanceCents'], 55000)
        acc = c.execute('SELECT * FROM stored_value_accounts WHERE club_id=1 AND user_id=7').fetchone()
        self.assertEqual(acc['balance_cents'], 55000)
        self.assertEqual(acc['total_recharged_cents'], 50000)
        self.assertEqual(acc['total_bonus_cents'], 5000)
        # 流水带余额快照，且两条都可审计
        led = c.execute('SELECT * FROM stored_value_ledger ORDER BY id').fetchall()
        self.assertEqual([x['balance_after_cents'] for x in led], [50000, 55000])

    def test_idempotent(self):
        c = _memory_db()
        kw = dict(club_id=1, user_id=7, delta_cents=10000, ledger_type='RECHARGE',
                  source_type='recharge', source_id='r1')
        self.assertTrue(sv.apply_ledger(c, **kw)['ok'])
        dup = sv.apply_ledger(c, **kw)
        self.assertFalse(dup['ok'])
        self.assertEqual(dup['reason'], 'duplicated')
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM stored_value_ledger').fetchone()['n'], 1)
        self.assertEqual(c.execute('SELECT balance_cents FROM stored_value_accounts').fetchone()[0], 10000)

    def test_insufficient(self):
        c = _memory_db()
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=1000, ledger_type='RECHARGE',
                        source_type='recharge', source_id='r1')
        bad = sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=-5000, ledger_type='PAY',
                              source_type='order', source_id='o1')
        self.assertFalse(bad['ok'])
        self.assertEqual(bad['reason'], 'insufficient')
        self.assertEqual(bad['balanceCents'], 1000)
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM stored_value_ledger').fetchone()['n'], 1)

    def test_pay(self):
        c = _memory_db()
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=10000, ledger_type='RECHARGE',
                        source_type='recharge', source_id='r1')
        pay = sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=-3000, ledger_type='PAY',
                              source_type='order', source_id='o1')
        self.assertTrue(pay['ok'])
        self.assertEqual(pay['balanceCents'], 7000)
        acc = c.execute('SELECT total_spent_cents FROM stored_value_accounts').fetchone()[0]
        self.assertEqual(acc, 3000)

    def test_manual_adjust_does_not_pollute_totals(self):
        """人工调整只改余额，不进「累计充值/累计消费」口径（后台对账用）。"""
        c = _memory_db()
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=10000, ledger_type='RECHARGE')
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=1000, ledger_type='ADJUST_UP')
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=-500, ledger_type='ADJUST_DOWN')
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=-3000, ledger_type='PAY')
        acc = c.execute('SELECT * FROM stored_value_accounts').fetchone()
        self.assertEqual(acc['balance_cents'], 7500)
        self.assertEqual(acc['total_recharged_cents'], 10000)
        self.assertEqual(acc['total_bonus_cents'], 0)
        self.assertEqual(acc['total_spent_cents'], 3000)

    def test_club_isolated(self):
        c = _memory_db()
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=10000, ledger_type='RECHARGE')
        sv.apply_ledger(c, club_id=2, user_id=7, delta_cents=20000, ledger_type='RECHARGE')
        rows = c.execute('SELECT club_id,balance_cents FROM stored_value_accounts ORDER BY club_id').fetchall()
        self.assertEqual([(r['club_id'], r['balance_cents']) for r in rows], [(1, 10000), (2, 20000)])

    def test_frozen(self):
        c = _memory_db()
        sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=10000, ledger_type='RECHARGE')
        c.execute("UPDATE stored_value_accounts SET status='FROZEN'")
        r = sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=1000, ledger_type='RECHARGE')
        self.assertEqual(r['reason'], 'frozen')

    def test_bad_type_and_zero(self):
        c = _memory_db()
        self.assertEqual(sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=0,
                                         ledger_type='RECHARGE')['reason'], 'zero-delta')
        self.assertEqual(sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=100,
                                         ledger_type='NOPE')['reason'], 'bad-type')
        self.assertEqual(c.execute('SELECT COUNT(*) n FROM stored_value_ledger').fetchone()['n'], 0)

    def test_no_source_id_always_distinct(self):
        c = _memory_db()
        for _ in range(2):
            self.assertTrue(sv.apply_ledger(c, club_id=1, user_id=7, delta_cents=1000,
                                            ledger_type='ADJUST_UP')['ok'])
        self.assertEqual(c.execute('SELECT balance_cents FROM stored_value_accounts').fetchone()[0], 2000)


if __name__ == '__main__':
    unittest.main()
