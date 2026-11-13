"""P3 结算编排单测：opt-in 归一、金额封顶、拼团快照、状态机与幂等、事件审计、汇总。"""
import importlib.util, os, sqlite3, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'checkout_settlement.py')
    spec = importlib.util.spec_from_file_location('checkout_settlement_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cs = _load()
NOW = 1763000000


def _db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    cs.ensure_tables(c)
    return c


def _plan(gift=2000, cash=10000, commission=True, group=None):
    return cs.build_plan(
        cash_cents=cash,
        req={'useGiftCard': bool(gift), 'giftCardCents': 0, 'teamId': 1, 'commission': commission},
        balance_cents=gift, max_ratio=100, group_quote=group, participant_count=2)


def _record(c, cid='CK1', club=1, user=10, plan=None, **kw):
    return cs.create_record(c, checkout_id=cid, club_id=club, user_id=user,
                            plan=plan or _plan(), request={'useGiftCard': True},
                            operator='test', now=NOW, **kw)


class TestMoney(unittest.TestCase):
    def test_to_cents_roundtrip(self):
        self.assertEqual(cs.to_cents(12.34), 1234)
        self.assertEqual(cs.to_cents('0.01'), 1)
        self.assertEqual(cs.from_cents(1234), 12.34)

    def test_to_cents_dirty_input_never_raises(self):
        for v in (None, '', 'abc', -5, float('nan'), float('inf'), float('-inf'), [], {}):
            self.assertEqual(cs.to_cents(v), 0, v)

    def test_to_cents_clamped(self):
        self.assertEqual(cs.to_cents(10 ** 9), cs.MAX_AMOUNT_CENTS)

    def test_from_cents_never_negative(self):
        self.assertEqual(cs.from_cents(-999), 0.0)


class TestNormalizeRequest(unittest.TestCase):
    def test_missing_or_bad_settlement_disables_everything(self):
        for p in (None, {}, {'settlement': None}, {'settlement': True},
                  {'settlement': 'x'}, {'settlement': []}, {'settlement': {}},
                  {'settlement': {'foo': 1}},
                  {'settlement': {'useGiftCard': False, 'teamId': 0, 'commission': False}}):
            self.assertIsNone(cs.normalize_request(p), p)

    def test_gift_card_opt_in(self):
        r = cs.normalize_request({'settlement': {'useGiftCard': True}})
        self.assertTrue(r['useGiftCard'])
        self.assertFalse(r['commission'])
        self.assertEqual(r['teamId'], 0)

    def test_team_only_opt_in(self):
        r = cs.normalize_request({'settlement': {'teamId': '42'}})
        self.assertEqual(r['teamId'], 42)
        self.assertFalse(r['useGiftCard'])

    def test_commission_only_opt_in(self):
        r = cs.normalize_request({'settlement': {'commission': True}})
        self.assertTrue(r['commission'])
        self.assertEqual(r['giftCardCents'], 0)

    def test_dirty_values_clamped_not_raised(self):
        r = cs.normalize_request({'settlement': {
            'useGiftCard': True, 'giftCardCents': -99, 'teamId': 'nope'}})
        self.assertEqual(r['giftCardCents'], 0)
        self.assertEqual(r['teamId'], 0)

    def test_whitelist_keys_only(self):
        r = cs.normalize_request({'settlement': {
            'useGiftCard': True, 'clubId': 999, 'cashAmount': 0.01}})
        self.assertEqual(set(r), {'useGiftCard', 'giftCardCents', 'teamId', 'commission'})


class TestQuota(unittest.TestCase):
    def test_triple_ceiling(self):
        q = cs.gift_card_quota(payable_cents=10000, balance_cents=3000)
        self.assertEqual(q['usableCents'], 3000)
        self.assertEqual(q['remainingCents'], 7000)

    def test_ratio_ceiling(self):
        q = cs.gift_card_quota(payable_cents=10000, balance_cents=90000, max_ratio=40)
        self.assertEqual(q['usableCents'], 4000)

    def test_requested_ceiling(self):
        q = cs.gift_card_quota(payable_cents=10000, balance_cents=90000,
                               requested_cents=1500)
        self.assertEqual(q['usableCents'], 1500)

    def test_zero_cases(self):
        self.assertEqual(cs.gift_card_quota(payable_cents=0, balance_cents=500)['usableCents'], 0)
        self.assertEqual(cs.gift_card_quota(payable_cents=500, balance_cents=0)['usableCents'], 0)
        self.assertEqual(cs.gift_card_quota(payable_cents=500, balance_cents=500,
                                            max_ratio=0)['usableCents'], 0)

    def test_never_raises_on_dirty_input(self):
        q = cs.gift_card_quota(payable_cents='abc', balance_cents=None, max_ratio=-3)
        self.assertEqual(q['usableCents'], 0)


class TestGroupBuySnapshot(unittest.TestCase):
    def test_success_with_real_saving(self):
        g = cs.group_buy_snapshot(
            {'status': 'SUCCESS', 'teamId': 7, 'campaignId': 3,
             'priceCents': 8000, 'originalPriceCents': 10000}, participant_count=2)
        self.assertEqual(g['savingCents'], 4000)
        self.assertTrue(g['appliedToPayable'])
        self.assertEqual(g['participantCount'], 2)

    def test_open_or_no_saving_not_applied(self):
        for q in ({'status': 'OPEN', 'priceCents': 8000, 'originalPriceCents': 10000},
                  {'status': 'SUCCESS', 'priceCents': 10000, 'originalPriceCents': 10000},
                  {'status': 'SUCCESS', 'priceCents': 12000, 'originalPriceCents': 10000},
                  {'status': 'SUCCESS', 'priceCents': 0, 'originalPriceCents': 10000},
                  None, {}, 'x'):
            self.assertIsNone(cs.group_buy_snapshot(q, 1), q)

    def test_participant_count_clamped(self):
        g = cs.group_buy_snapshot(
            {'status': 'SUCCESS', 'priceCents': 1, 'originalPriceCents': 2}, -5)
        self.assertEqual(g['participantCount'], 1)


class TestBuildPlan(unittest.TestCase):
    def test_disabled_plan_keeps_cash_untouched(self):
        p = cs.build_plan(cash_cents=10000, req=None)
        self.assertFalse(p['enabled'])
        self.assertEqual(p['cashAfterCents'], 10000)
        self.assertEqual(p['giftCardCents'], 0)
        self.assertIsNone(p['giftCard'])

    def test_gift_card_reduces_cash_and_keeps_commission_base(self):
        p = cs.build_plan(cash_cents=10000,
                          req={'useGiftCard': True, 'commission': True},
                          balance_cents=2500)
        self.assertEqual(p['giftCardCents'], 2500)
        self.assertEqual(p['cashAfterCents'], 7500)
        # 礼品卡是俱乐部已收的预付资金，佣金基数不扣掉它
        self.assertEqual(p['orderAmountCents'], 10000)
        self.assertTrue(p['commissionEnabled'])

    def test_plan_with_group_snapshot(self):
        p = _plan(group={'status': 'SUCCESS', 'priceCents': 8000,
                         'originalPriceCents': 10000})
        self.assertEqual(p['groupBuy']['savingCents'], 4000)
        self.assertEqual(p['groupBuyCents'], 4000)
        # 10000 → 拼团立减 4000 → 6000；礼品卡只有 2000 → 实收 4000
        self.assertEqual(p['orderAmountCents'], 6000)
        self.assertEqual(p['cashAfterCents'], 4000)

    def test_group_discount_capped_by_cash(self):
        p = cs.build_plan(cash_cents=1000, req={'teamId': 1},
                          group_quote={'status': 'SUCCESS', 'priceCents': 1000,
                                       'originalPriceCents': 100000})
        self.assertEqual(p['groupBuyCents'], 1000)
        self.assertEqual(p['cashAfterCents'], 0)

    def test_gift_card_only_applies_to_post_group_amount(self):
        p = cs.build_plan(cash_cents=10000,
                          req={'useGiftCard': True, 'teamId': 1},
                          balance_cents=9000,
                          group_quote={'status': 'SUCCESS', 'priceCents': 8000,
                                       'originalPriceCents': 10000},
                          participant_count=2)
        self.assertEqual(p['groupBuyCents'], 4000)
        self.assertEqual(p['giftCardCents'], 6000)
        self.assertEqual(p['cashAfterCents'], 0)
        self.assertEqual(p['orderAmountCents'], 6000)

    def test_cash_never_negative(self):
        p = cs.build_plan(cash_cents=100, req={'useGiftCard': True}, balance_cents=10 ** 9)
        self.assertEqual(p['cashAfterCents'], 0)


class TestRecords(unittest.TestCase):
    def setUp(self):
        self.c = _db()

    def tearDown(self):
        self.c.close()

    def test_create_and_view(self):
        r = _record(self.c)
        self.assertTrue(r['ok'])
        v = cs.view(cs.get_record(self.c, 'CK1'))
        self.assertEqual(v['status'], 'RESERVED')
        self.assertEqual(v['giftCardCents'], 2000)
        self.assertEqual(v['groupBuyCents'], 0)
        self.assertEqual(v['cashAfter'], 80.0)
        self.assertEqual(v['orderAmount'], 100.0)

    def test_create_is_idempotent(self):
        _record(self.c)
        again = _record(self.c, plan=_plan(gift=9999))
        self.assertFalse(again['ok'])
        self.assertEqual(again['reason'], 'duplicated')
        self.assertEqual(cs.view(cs.get_record(self.c, 'CK1'))['giftCardCents'], 2000)

    def test_create_rejects_empty_checkout_id(self):
        self.assertEqual(cs.create_record(self.c, checkout_id='  ', club_id=1,
                                          user_id=1, plan=_plan())['reason'],
                         'invalid-checkout')

    def test_settle_and_repeat_is_idempotent(self):
        _record(self.c)
        first = cs.settle_record(self.c, 'CK1', registration_id=88,
                                 commission_cents=500, commission_reason='ok', now=NOW)
        self.assertTrue(first['ok'])
        again = cs.settle_record(self.c, 'CK1', registration_id=88,
                                 commission_cents=999, now=NOW)
        self.assertTrue(again['idempotent'])
        self.assertEqual(again['commissionCents'], 500)
        v = cs.view(cs.get_record(self.c, 'CK1'))
        self.assertEqual(v['status'], 'SETTLED')
        self.assertEqual(v['registrationId'], 88)
        self.assertEqual(v['commissionAmount'], 5.0)

    def test_settle_unknown_checkout(self):
        self.assertEqual(cs.settle_record(self.c, 'NOPE')['reason'], 'no-record')

    def test_release_then_settle_rejected(self):
        _record(self.c)
        rel = cs.release_record(self.c, 'CK1', reason='cancel', now=NOW)
        self.assertTrue(rel['ok'])
        self.assertEqual(rel['giftCardCents'], 2000)
        st = cs.settle_record(self.c, 'CK1', registration_id=1, now=NOW)
        self.assertEqual(st['reason'], 'bad-status')

    def test_settled_record_cannot_be_released(self):
        _record(self.c)
        cs.settle_record(self.c, 'CK1', registration_id=1, now=NOW)
        self.assertEqual(cs.release_record(self.c, 'CK1')['reason'], 'bad-status')

    def test_failed_then_retry_settles(self):
        _record(self.c)
        self.assertTrue(cs.fail_record(self.c, 'CK1', reason='commission exception')['ok'])
        self.assertEqual(cs.view(cs.get_record(self.c, 'CK1'))['status'], 'FAILED')
        r = cs.settle_record(self.c, 'CK1', registration_id=5,
                             commission_cents=100, now=NOW)
        self.assertTrue(r['ok'])
        ev = list(reversed(cs.list_events(self.c, 'CK1')))
        self.assertEqual([x['eventType'] for x in ev], ['RESERVE', 'FAIL', 'RETRY'])
        self.assertEqual(ev[-1]['toStatus'], 'SETTLED')

    def test_events_track_full_lifecycle(self):
        _record(self.c)
        cs.settle_record(self.c, 'CK1', registration_id=1, now=NOW)
        ev = cs.list_events(self.c, 'CK1')
        self.assertEqual([x['eventType'] for x in reversed(ev)], ['RESERVE', 'SETTLE'])
        self.assertEqual(ev[0]['fromStatus'], 'RESERVED')

    def test_cross_club_isolation_and_filters(self):
        _record(self.c, 'CK1', club=1)
        _record(self.c, 'CK2', club=2)
        _record(self.c, 'CK3', club=2)
        cs.settle_record(self.c, 'CK3', registration_id=3, now=NOW)
        self.assertEqual(len(cs.list_records(self.c, club_id=1)), 1)
        self.assertEqual(len(cs.list_records(self.c, club_id=2)), 2)
        self.assertEqual(len(cs.list_records(self.c, status='settled')), 1)
        self.assertEqual(cs.summary(self.c, club_id=2)['total'], 2)

    def test_summary_amounts(self):
        _record(self.c, 'CK1', plan=_plan(gift=2000))
        _record(self.c, 'CK2', plan=_plan(gift=1000))
        cs.settle_record(self.c, 'CK1', registration_id=1, commission_cents=300, now=NOW)
        s = cs.summary(self.c)
        self.assertEqual(s['total'], 2)
        self.assertEqual(s['giftCardCents'], 3000)
        self.assertEqual(s['groupBuyCents'], 0)
        self.assertEqual(s['commissionCents'], 300)
        self.assertEqual(s['cashAfterCents'], 17000)
        self.assertEqual(s['byStatus']['SETTLED'], 1)
        self.assertEqual(s['byStatus']['RESERVED'], 1)

    def test_all_dao_helpers_never_raise_on_missing_table(self):
        bare = sqlite3.connect(':memory:')
        bare.row_factory = sqlite3.Row
        self.assertIsNone(cs.get_record(bare, 'X'))
        self.assertEqual(cs.list_records(bare), [])
        self.assertEqual(cs.list_events(bare, 'X'), [])
        self.assertEqual(cs.summary(bare)['total'], 0)
        # get_record 内部吞掉 missing-table 异常 → 上层得到 no-record，同样不抛
        self.assertEqual(cs.settle_record(bare, 'X')['reason'], 'no-record')
        self.assertEqual(cs.release_record(bare, 'X')['reason'], 'no-record')
        bare.close()

    def test_bad_event_type_rejected(self):
        self.assertFalse(cs.add_event(self.c, checkout_id='CK1', club_id=1,
                                      event_type='HACK')['ok'])


if __name__ == '__main__':
    unittest.main()
