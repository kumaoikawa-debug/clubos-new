"""gift_card 礼品卡单测：面额/卡密归一化、惰性有效期、发卡、兑换幂等、
余额核销与冲回、只读试算、异常不阻断。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'gift_card.py')
    spec = importlib.util.spec_from_file_location('gift_card_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gc = _load()
NOW = 1763000000        # 固定时间戳
TODAY = '2026-11-13'    # 固定业务日


def _memory_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE gift_card_configs (
        club_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
        config_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT)""")
    c.execute("""CREATE TABLE gift_card_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        name TEXT NOT NULL, face_cents INTEGER NOT NULL DEFAULT 0,
        plan_count INTEGER NOT NULL DEFAULT 0, valid_from TEXT, valid_until TEXT,
        status TEXT NOT NULL DEFAULT 'ACTIVE', note TEXT, created_at TEXT, created_by TEXT)""")
    c.execute("""CREATE TABLE gift_card_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL, code TEXT NOT NULL,
        face_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'ACTIVE', user_id INTEGER, redeemed_at TEXT,
        valid_from TEXT, valid_until TEXT, note TEXT, created_at TEXT,
        UNIQUE(code))""")
    c.execute("""CREATE TABLE gift_card_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, balance_cents INTEGER NOT NULL DEFAULT 0,
        total_redeemed_cents INTEGER NOT NULL DEFAULT 0,
        total_consumed_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT, updated_at TEXT,
        UNIQUE(club_id, user_id))""")
    c.execute("""CREATE TABLE gift_card_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, type TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        balance_after_cents INTEGER NOT NULL DEFAULT 0,
        source_type TEXT, source_id TEXT, card_id INTEGER, operator TEXT,
        note TEXT, created_at TEXT,
        UNIQUE(club_id, user_id, source_type, source_id))""")
    return c


def _batch(c, club=1, face=10000, count=3, status='ACTIVE', vf=None, vu=None, name='双十一礼品卡'):
    cur = c.execute("""INSERT INTO gift_card_batches
        (club_id,name,face_cents,plan_count,valid_from,valid_until,status,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",
                    (club, name, face, count, vf, vu, status, gc._ts_to_str(NOW)))
    return cur.lastrowid


def _cards(c, batch, club=1, face=10000, count=3, vf=None, vu=None, status='ACTIVE'):
    return gc.issue_cards(c, club_id=club, batch_id=batch, face_cents=face,
                          count=count, valid_from=vf, valid_until=vu, now=NOW)['codes']


def _cfg(**kw):
    d = gc.default_config()
    d['enabled'] = True
    d.update(kw)
    return d


class TestBasics(unittest.TestCase):
    def test_to_cents_and_back(self):
        self.assertEqual(gc.to_cents(100), 10000)
        self.assertEqual(gc.to_cents(0.01), 1)
        self.assertEqual(gc.to_cents(99.99), 9999)
        self.assertEqual(gc.from_cents(12345), 123.45)
        for bad in ('abc', -1, 0, float('nan'), float('inf'), None, 999999999):
            self.assertEqual(gc.to_cents(bad), 0, bad)

    def test_ts_accepts_2026_epoch(self):
        # P2-1 教训：秒级时间戳上界必须 > 1.78e9，否则显式 now 被静默洗掉。
        self.assertEqual(gc._ts(NOW), NOW)
        self.assertEqual(gc._ts(1), 1)
        self.assertTrue(gc._ts(None) > 0)

    def test_code_normalize_and_generate(self):
        self.assertEqual(gc.normalize_code(' gc-ab12 cd34 '), 'GCAB12CD34')
        self.assertEqual(gc.normalize_code(''), '')
        self.assertEqual(gc.normalize_code('短'), '')
        self.assertEqual(gc.normalize_code('AB'), '')          # 太短
        self.assertEqual(gc.normalize_code('A' * 33), '')     # 太长
        codes = {gc.gen_code() for _ in range(30)}
        self.assertEqual(len(codes), 30)
        for c in codes:
            self.assertTrue(c.startswith('GC'))
            self.assertEqual(len(c), 2 + gc.CODE_LENGTH)
            self.assertTrue(set(c) <= set(gc.CODE_ALPHABET + 'GC'))

    def test_business_today_injectable(self):
        self.assertEqual(gc.business_today('2026-01-02T09:00:00'), '2026-01-02')
        self.assertEqual(len(gc.business_today()), 10)

    def test_parse_date_strict(self):
        self.assertEqual(gc.parse_date('2026-1-2'), '2026-01-02')
        self.assertIsNone(gc.parse_date(''))
        self.assertIsNone(gc.parse_date(None))
        self.assertIs(gc.parse_date('2026-13-40'), gc._INVALID)
        self.assertIs(gc.parse_date('not-a-date'), gc._INVALID)


class TestNormalize(unittest.TestCase):
    def test_config_merge_keeps_notice(self):
        cfg = gc.normalize_config({'enabled': True, 'notice': '可用于活动报名'})
        again = gc.normalize_config({'enabled': False}, cfg)
        self.assertFalse(again['enabled'])
        self.assertEqual(again['notice'], '可用于活动报名')
        self.assertEqual(gc.config_view(again)['maxRedeemsPerUser'], 0)

    def test_config_from_row(self):
        d = {'enabled': 1, 'config_json': gc.dumps({'notice': 'hi', 'maxRedeemsPerUser': 3})}
        self.assertTrue(gc.config_from_row(d)['enabled'])
        self.assertEqual(gc.config_from_row(d)['maxRedeemsPerUser'], 3)
        self.assertFalse(gc.config_from_row(None)['enabled'])
        self.assertEqual(gc.public_config(_cfg(notice='x'))['notice'], 'x')

    def test_batch_strict(self):
        b = gc.normalize_batch({'name': '年卡', 'faceAmount': 200, 'count': 5,
                                'validFrom': '2026-01-01', 'validUntil': '2026-12-31'})
        self.assertEqual(b['faceCents'], 20000)
        self.assertEqual(b['faceAmount'], 200.0)
        self.assertEqual(b['count'], 5)
        self.assertEqual(b['validFrom'], '2026-01-01')
        for bad in ({'name': '', 'faceAmount': 100, 'count': 1},
                    {'name': 'x', 'faceAmount': 0, 'count': 1},
                    {'name': 'x', 'faceAmount': 100001, 'count': 1},
                    {'name': 'x', 'faceAmount': 100, 'count': 0},
                    {'name': 'x', 'faceAmount': 100, 'count': gc.MAX_ISSUE + 1},
                    {'name': 'x', 'faceAmount': 100, 'count': 1, 'validFrom': 'oops'},
                    {'name': 'x', 'faceAmount': 100, 'count': 1,
                     'validFrom': '2026-12-31', 'validUntil': '2026-01-01'}):
            self.assertIsNone(gc.normalize_batch(bad), bad)

    def test_batch_update_cannot_change_face(self):
        cur = {'validFrom': '2026-01-01', 'validUntil': '2026-12-31', 'status': 'ACTIVE'}
        out = gc.normalize_batch_update({'name': '改名', 'status': 'VOID',
                                         'faceAmount': 99999}, cur)
        self.assertEqual(out['status'], 'VOID')
        self.assertNotIn('faceCents', out)   # 面额字段被忽略，不回落也不夹逼
        self.assertIsNone(gc.normalize_batch_update({'status': 'REDEEMED'}, cur))
        self.assertIsNone(gc.normalize_batch_update({'validUntil': '2025-12-31'}, cur))

    def test_card_update_blocks_redeemed(self):
        self.assertIsNone(gc.normalize_card_update({'status': 'ACTIVE'}, {'status': 'REDEEMED'}))
        self.assertEqual(gc.normalize_card_update({'status': 'FROZEN'}, {'status': 'ACTIVE'})['status'], 'FROZEN')
        self.assertIsNone(gc.normalize_card_update({'status': 'EXPIRED'}, {'status': 'ACTIVE'}))

    def test_effective_status_lazy(self):
        card = {'status': 'ACTIVE', 'valid_from': None, 'valid_until': None}
        self.assertEqual(gc.effective_status(card, TODAY), 'ACTIVE')
        self.assertEqual(gc.effective_status(dict(card, valid_until='2026-11-12'), TODAY), 'EXPIRED')
        self.assertEqual(gc.effective_status(dict(card, valid_from='2026-11-14'), TODAY), 'NOT_STARTED')
        self.assertEqual(gc.effective_status(dict(card, valid_until='2026-11-13'), TODAY), 'ACTIVE')
        self.assertEqual(gc.effective_status(dict(card, status='VOID'), TODAY), 'VOID')

    def test_quote_consume_is_pure(self):
        q = gc.quote_consume(5000, 3000)
        self.assertEqual(q['usableCents'], 3000)
        self.assertEqual(q['remainingCents'], 2000)
        self.assertEqual(gc.quote_consume(10000, 99999)['usableCents'], 10000)
        self.assertEqual(gc.quote_consume(10000, 99999, 50)['usableCents'], 5000)
        self.assertEqual(gc.quote_consume('x', None)['usableCents'], 0)


class TestIssueAndRedeem(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        self.bid = _batch(self.c)

    def tearDown(self):
        self.c.close()

    def test_issue_count_and_uniqueness(self):
        codes = _cards(self.c, self.bid, count=7)
        self.assertEqual(len(codes), 7)
        self.assertEqual(len(set(codes)), 7)
        n = self.c.execute('SELECT COUNT(*) FROM gift_card_cards WHERE batch_id=?', (self.bid,)).fetchone()[0]
        self.assertEqual(n, 7)
        self.assertEqual(gc.issue_cards(self.c, club_id=1, batch_id=self.bid,
                                        face_cents=10000, count=0)['reason'], 'invalid-input')

    def test_redeem_success_and_balance(self):
        code = _cards(self.c, self.bid, count=1)[0]
        res = gc.redeem(self.c, club_id=1, code=code, user_id=10,
                        cfg=_cfg(), today=TODAY, now=NOW)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['faceCents'], 10000)
        self.assertEqual(res['balanceCents'], 10000)
        acc = self.c.execute('SELECT * FROM gift_card_accounts WHERE club_id=1 AND user_id=10').fetchone()
        self.assertEqual(acc['balance_cents'], 10000)
        self.assertEqual(acc['total_redeemed_cents'], 10000)
        led = self.c.execute('SELECT * FROM gift_card_ledger ORDER BY id').fetchall()
        self.assertEqual(len(led), 1)
        self.assertEqual(led[0]['type'], 'REDEEM')
        self.assertEqual(led[0]['balance_after_cents'], 10000)
        card = self.c.execute('SELECT * FROM gift_card_cards WHERE code=?', (code,)).fetchone()
        self.assertEqual(card['status'], 'REDEEMED')
        self.assertEqual(card['user_id'], 10)

    def test_redeem_is_idempotent(self):
        code = _cards(self.c, self.bid, count=1)[0]
        gc.redeem(self.c, club_id=1, code=code, user_id=10, cfg=_cfg(), today=TODAY, now=NOW)
        res = gc.redeem(self.c, club_id=1, code=code, user_id=10, cfg=_cfg(), today=TODAY, now=NOW)
        self.assertFalse(res['ok'])
        self.assertIn(res['reason'], ('card-redeemed', 'duplicated'))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM gift_card_ledger').fetchone()[0], 1)

    def test_redeem_reasons(self):
        codes = _cards(self.c, self.bid, count=4)
        # 未开启 / 中枢关闭
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[0], user_id=10,
                                   cfg=gc.default_config(), today=TODAY)['reason'], 'disabled')
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[0], user_id=10,
                                   cfg=_cfg(), module_enabled=False, today=TODAY)['reason'], 'disabled')
        # 坏卡密 / 不存在 / 跨俱乐部
        self.assertEqual(gc.redeem(self.c, club_id=1, code='!', user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'bad-code')
        self.assertEqual(gc.redeem(self.c, club_id=1, code='GCZZZZZZZZZZ', user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'not-found')
        self.assertEqual(gc.redeem(self.c, club_id=2, code=codes[0], user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'not-found')
        # 非法用户
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[0], user_id=0,
                                   cfg=_cfg(), today=TODAY)['reason'], 'invalid-user')
        # 冻结 / 作废
        self.c.execute("UPDATE gift_card_cards SET status='FROZEN' WHERE code=?", (codes[0],))
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[0], user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-frozen')
        self.c.execute("UPDATE gift_card_cards SET status='VOID' WHERE code=?", (codes[1],))
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[1], user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-void')
        # 已兑换 / 批次停用
        gc.redeem(self.c, club_id=1, code=codes[2], user_id=10, cfg=_cfg(), today=TODAY, now=NOW)
        self.c.execute('UPDATE gift_card_batches SET status=? WHERE id=?', ('VOID', self.bid))
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[3], user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'batch-void')

    def test_redeem_expiry_and_not_started(self):
        # 卡自带有效期（真实发卡时从批次复制）
        past = _cards(self.c, _batch(self.c, vu='2026-11-12', name='过期批次'),
                      count=1, vu='2026-11-12')[0]
        self.assertEqual(gc.redeem(self.c, club_id=1, code=past, user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-expired')
        future = _cards(self.c, _batch(self.c, vf='2026-11-14', name='未开始批次'),
                        count=1, vf='2026-11-14')[0]
        self.assertEqual(gc.redeem(self.c, club_id=1, code=future, user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-not-started')

    def test_batch_expiry_is_hard_gate_for_issued_cards(self):
        """后台事后收紧批次有效期，已发出的旧卡不能绕过（双真相防护）。"""
        code = _cards(self.c, self.bid, count=1)[0]
        self.c.execute("UPDATE gift_card_batches SET valid_until='2026-11-12' WHERE id=?", (self.bid,))
        self.assertEqual(gc.redeem(self.c, club_id=1, code=code, user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-expired')
        self.c.execute("UPDATE gift_card_batches SET valid_until=NULL, valid_from='2026-11-14' WHERE id=?", (self.bid,))
        self.assertEqual(gc.redeem(self.c, club_id=1, code=code, user_id=10,
                                   cfg=_cfg(), today=TODAY)['reason'], 'card-not-started')

    def test_redeem_limit_per_user(self):
        codes = _cards(self.c, self.bid, count=3)
        cfg = _cfg(maxRedeemsPerUser=2)
        for code in codes[:2]:
            self.assertTrue(gc.redeem(self.c, club_id=1, code=code, user_id=10,
                                      cfg=cfg, today=TODAY, now=NOW)['ok'])
        self.assertEqual(gc.redeem(self.c, club_id=1, code=codes[2], user_id=10,
                                   cfg=cfg, today=TODAY)['reason'], 'redeem-limit')
        # 别的会员不受影响
        self.assertTrue(gc.redeem(self.c, club_id=1, code=codes[2], user_id=11,
                                  cfg=cfg, today=TODAY, now=NOW)['ok'])

    def test_two_cards_accumulate(self):
        codes = _cards(self.c, self.bid, count=2)
        for code in codes:
            gc.redeem(self.c, club_id=1, code=code, user_id=10, cfg=_cfg(), today=TODAY, now=NOW)
        acc = self.c.execute('SELECT * FROM gift_card_accounts WHERE user_id=10').fetchone()
        self.assertEqual(acc['balance_cents'], 20000)
        self.assertEqual(acc['total_redeemed_cents'], 20000)

    def test_views(self):
        codes = _cards(self.c, self.bid, count=1)
        bv = gc.batch_view(self.c.execute('SELECT * FROM gift_card_batches WHERE id=?', (self.bid,)).fetchone(), 1, 0)
        self.assertEqual(bv['faceAmount'], 100.0)
        self.assertEqual(bv['issued'], 1)
        cv = gc.card_view(self.c.execute('SELECT * FROM gift_card_cards WHERE code=?', (codes[0],)).fetchone(), TODAY)
        self.assertEqual(cv['effectiveStatus'], 'ACTIVE')
        self.assertEqual(cv['faceAmount'], 100.0)


class TestConsume(unittest.TestCase):
    def setUp(self):
        self.c = _memory_db()
        self.bid = _batch(self.c)
        self.code = _cards(self.c, self.bid, count=1)[0]
        gc.redeem(self.c, club_id=1, code=self.code, user_id=10,
                  cfg=_cfg(), today=TODAY, now=NOW)

    def tearDown(self):
        self.c.close()

    def test_consume_success(self):
        res = gc.consume(self.c, club_id=1, user_id=10, cents=3000,
                         order_type='registration', order_id='R-1', now=NOW)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['balanceCents'], 7000)
        acc = self.c.execute('SELECT * FROM gift_card_accounts WHERE user_id=10').fetchone()
        self.assertEqual(acc['total_consumed_cents'], 3000)
        led = self.c.execute("SELECT * FROM gift_card_ledger WHERE type='CONSUME'").fetchone()
        self.assertEqual(led['balance_after_cents'], 7000)
        self.assertEqual(led['source_id'], 'registration:R-1')

    def test_consume_idempotent_and_insufficient(self):
        gc.consume(self.c, club_id=1, user_id=10, cents=3000,
                   order_type='registration', order_id='R-1', now=NOW)
        dup = gc.consume(self.c, club_id=1, user_id=10, cents=3000,
                         order_type='registration', order_id='R-1', now=NOW)
        self.assertFalse(dup['ok'])
        self.assertEqual(dup['reason'], 'duplicated')
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM gift_card_ledger WHERE type='CONSUME'").fetchone()[0], 1)
        big = gc.consume(self.c, club_id=1, user_id=10, cents=999999,
                         order_type='camp_order', order_id='G-1', now=NOW)
        self.assertEqual(big['reason'], 'insufficient')
        # 营销适用范围硬边界：装备订单一律拒绝核销（不让「金额不足」掩盖越界）
        gear = gc.consume(self.c, club_id=1, user_id=10, cents=100,
                          order_type='gear_order', order_id='G-2', now=NOW)
        self.assertEqual(gear['reason'], 'invalid-input')
        self.assertEqual(self.c.execute('SELECT balance_cents FROM gift_card_accounts WHERE user_id=10').fetchone()[0], 7000)

    def test_consume_invalid_input(self):
        for kw in ({'cents': 0}, {'cents': -1}, {'cents': gc.MAX_AMOUNT_CENTS + 1},
                   {'order_type': 'nope'}, {'order_id': ''}):
            args = dict(club_id=1, user_id=10, cents=100,
                        order_type='custom', order_id='X-1', now=NOW)
            args.update(kw)
            self.assertEqual(gc.consume(self.c, **args)['reason'], 'invalid-input', kw)

    def test_frozen_account_rejects(self):
        self.c.execute("UPDATE gift_card_accounts SET status='FROZEN' WHERE user_id=10")
        self.assertEqual(gc.consume(self.c, club_id=1, user_id=10, cents=100,
                                    order_type='custom', order_id='X-1')['reason'], 'frozen')

    def test_reverse_consume(self):
        gc.consume(self.c, club_id=1, user_id=10, cents=4000,
                   order_type='registration', order_id='R-9', now=NOW)
        res = gc.reverse_consume(self.c, club_id=1, user_id=10,
                                 order_type='registration', order_id='R-9', now=NOW)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['cents'], 4000)
        self.assertEqual(res['balanceCents'], 10000)
        acc = self.c.execute('SELECT * FROM gift_card_accounts WHERE user_id=10').fetchone()
        self.assertEqual(acc['total_consumed_cents'], 0)   # 净口径：冲回后归零
        dup = gc.reverse_consume(self.c, club_id=1, user_id=10,
                                 order_type='registration', order_id='R-9', now=NOW)
        self.assertEqual(dup['reason'], 'duplicated')
        missing = gc.reverse_consume(self.c, club_id=1, user_id=10,
                                     order_type='registration', order_id='NOPE', now=NOW)
        self.assertEqual(missing['reason'], 'not-found')

    def test_adjust_does_not_pollute_totals(self):
        r = gc.apply_ledger(self.c, club_id=1, user_id=10, delta_cents=500,
                            ledger_type='ADJUST_UP', source_type='ADMIN',
                            source_id='manual-1', now=NOW)
        self.assertTrue(r['ok'])
        acc = self.c.execute('SELECT * FROM gift_card_accounts WHERE user_id=10').fetchone()
        self.assertEqual(acc['balance_cents'], 10500)
        self.assertEqual(acc['total_redeemed_cents'], 10000)
        self.assertEqual(acc['total_consumed_cents'], 0)

    def test_apply_ledger_zero_and_bad_type(self):
        self.assertEqual(gc.apply_ledger(self.c, club_id=1, user_id=10, delta_cents=0,
                                         ledger_type='REDEEM')['reason'], 'zero-delta')
        self.assertEqual(gc.apply_ledger(self.c, club_id=1, user_id=10, delta_cents=1,
                                         ledger_type='WHAT')['reason'], 'bad-type')


class TestExceptionSafety(unittest.TestCase):
    def test_missing_tables_never_raise(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        r = gc.redeem(c, club_id=1, code='GCABCD123456', user_id=10, cfg=_cfg(), today=TODAY)
        self.assertFalse(r['ok'])
        self.assertEqual(r['reason'], 'exception')
        self.assertEqual(gc.issue_cards(c, club_id=1, batch_id=1, face_cents=100,
                                        count=1)['reason'], 'exception')
        self.assertEqual(gc.consume(c, club_id=1, user_id=10, cents=100,
                                    order_type='custom', order_id='X')['reason'], 'exception')
        self.assertEqual(gc.reverse_consume(c, club_id=1, user_id=10,
                                            order_type='custom', order_id='X')['reason'], 'exception')
        c.close()


if __name__ == '__main__':
    unittest.main()
