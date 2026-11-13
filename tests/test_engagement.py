"""P1-2 互动玩法 domain 单测（纯函数 + 内存 sqlite 记账）。

用 importlib 直接加载 clubos_domain/engagement.py，避免触发包级 __init__ 的重依赖。
"""
import importlib.util
import os
import sqlite3
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'clubos_domain', 'engagement.py')
_spec = importlib.util.spec_from_file_location('engagement_under_test', _PATH)
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

DDL = """
CREATE TABLE engagement_configs (
  club_id INTEGER NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT, UNIQUE(club_id,kind));
CREATE TABLE engagement_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, kind TEXT NOT NULL,
  user_id INTEGER NOT NULL, biz_key TEXT NOT NULL, day_key TEXT, action TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(club_id,kind,user_id,biz_key));
CREATE TABLE engagement_rewards (
  id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, kind TEXT NOT NULL,
  user_id INTEGER NOT NULL, log_id INTEGER, reward_type TEXT NOT NULL DEFAULT 'POINTS',
  points INTEGER NOT NULL DEFAULT 0, prize_id TEXT, prize_name TEXT,
  status TEXT NOT NULL DEFAULT 'GRANTED', source_key TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, fulfilled_at TEXT);
CREATE UNIQUE INDEX idx_egr_idem ON engagement_rewards(club_id,kind,user_id,source_key)
  WHERE source_key IS NOT NULL;
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT);
CREATE TABLE clubs (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE club_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  level TEXT NOT NULL DEFAULT '普通会员', club_points_balance INTEGER NOT NULL DEFAULT 0,
  UNIQUE(club_id,user_id));
CREATE TABLE club_point_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER, user_id INTEGER, type TEXT,
  amount INTEGER, source_type TEXT, source_id TEXT, note TEXT, created_at TEXT);
"""


def _db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    c.execute("INSERT INTO clubs(id,name) VALUES(1,'远拓')")
    c.execute("INSERT INTO clubs(id,name) VALUES(2,'别家')")
    for u in (7, 8):
        c.execute("INSERT INTO users(id,name,phone) VALUES(?,?,?)", (u, 'u%d' % u, '1380000000%d' % u))
    return c


def _save(c, club_id, kind, cfg):
    return eng.save_config(c, club_id, kind, eng.normalize_config(kind, cfg))


class PureFunctions(unittest.TestCase):
    def test_day_key_uses_cn_tz(self):
        ts = eng.datetime(2026, 11, 13, 0, 30, tzinfo=eng.CN_TZ).timestamp()
        # UTC 此刻仍是 11-12，业务日必须是 11-13
        self.assertEqual(eng.day_key(ts), '2026-11-13')

    def test_compute_streak_continuous(self):
        days = ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13']
        self.assertEqual(eng.compute_streak(days, '2026-11-13'), 4)

    def test_compute_streak_not_signed_today(self):
        days = ['2026-11-10', '2026-11-11', '2026-11-12']
        self.assertEqual(eng.compute_streak(days, '2026-11-13'), 3)
        self.assertEqual(eng.compute_streak(days, '2026-11-15'), 0)

    def test_compute_streak_break(self):
        days = ['2026-11-09', '2026-11-11', '2026-11-12']
        self.assertEqual(eng.compute_streak(days, '2026-11-12'), 2)

    def test_cycle_day_wraps(self):
        self.assertEqual(eng.cycle_day(8, 7), 1)
        self.assertEqual(eng.cycle_day(7, 7), 7)
        self.assertEqual(eng.cycle_day(0, 7), 0)

    def test_checkin_reward_hits_highest_rule(self):
        cfg = {'dailyPoints': 5, 'cycleDays': 7,
               'streakRules': [{'days': 2, 'points': 10, 'enabled': True},
                               {'days': 3, 'points': 30, 'enabled': True}]}
        r = eng.checkin_reward(3, cfg)
        self.assertEqual(r['totalPoints'], 35)
        self.assertEqual(r['bonusPoints'], 30)
        self.assertEqual(r['hitRuleDays'], 3)

    def test_checkin_reward_skips_disabled_rule(self):
        cfg = {'dailyPoints': 5, 'cycleDays': 30,
               'streakRules': [{'days': 2, 'points': 99, 'enabled': False}]}
        self.assertEqual(eng.checkin_reward(2, cfg)['totalPoints'], 5)

    def test_draw_prize_deterministic(self):
        prizes = [{'id': 'a', 'name': 'A', 'weight': 1, 'stock': -1, 'enabled': True},
                  {'id': 'b', 'name': 'B', 'weight': 1, 'stock': -1, 'enabled': True}]
        self.assertEqual(eng.draw_prize(prizes, {}, lambda: 0.0)['id'], 'a')
        self.assertEqual(eng.draw_prize(prizes, {}, lambda: 0.999)['id'], 'b')

    def test_draw_prize_respects_stock(self):
        prizes = [{'id': 'a', 'name': 'A', 'weight': 1, 'stock': 2, 'enabled': True},
                  {'id': 'b', 'name': 'B', 'weight': 1, 'stock': -1, 'enabled': True}]
        self.assertEqual(eng.draw_prize(prizes, {'a': 2}, lambda: 0.0)['id'], 'b')
        self.assertIsNone(eng.draw_prize([prizes[0]], {'a': 2}))

    def test_draw_prize_skips_bad_items(self):
        self.assertIsNone(eng.draw_prize([
            {'id': 'x', 'name': 'X', 'weight': 0, 'enabled': True},
            {'id': 'y', 'name': 'Y', 'weight': 5, 'enabled': False}]))

    def test_normalize_prize(self):
        p = eng.normalize_prize({'name': '保温杯', 'type': 'PRIZE', 'stock': 3,
                                 'weight': 10, 'points': 0}, 0)
        self.assertEqual(p['id'], 'p1')
        self.assertEqual(p['type'], 'PRIZE')
        self.assertEqual(p['stock'], 3)
        self.assertIsNone(eng.normalize_prize({'weight': 5}))
        self.assertEqual(eng.normalize_prize({'name': 'X', 'type': 'BAD'})['type'], 'POINTS')

    def test_normalize_streak_rule_and_action(self):
        self.assertIsNone(eng.normalize_streak_rule({'days': 0, 'points': 5}))
        self.assertEqual(eng.normalize_streak_rule({'days': 7, 'points': 50})['days'], 7)
        self.assertEqual(eng.normalize_action({'code': 'SignUp', 'label': '报名'})['code'], 'signup')
        self.assertIsNone(eng.normalize_action({'code': 'bad code!'}))

    def test_default_configs(self):
        self.assertFalse(eng.default_config('CHECKIN')['enabled'])
        self.assertEqual(eng.default_config('LOTTERY')['dailyLimit'], 3)
        self.assertEqual(len(eng.default_config('STAMP')['actions']), 3)

    def test_normalize_config_bad_kind(self):
        with self.assertRaises(ValueError):
            eng.normalize_config('NOPE', {})

    def test_patch_enabled_keeps_prizes(self):
        """P1-1 踩过的坑回归：只改 enabled 不得丢失/改写既有复杂配置。"""
        cfg = eng.normalize_config('LOTTERY', {
            'enabled': True, 'dailyLimit': 5,
            'prizes': [{'name': 'A', 'points': 10, 'stock': 2, 'weight': 1},
                       {'name': 'B', 'type': 'PRIZE', 'stock': 1, 'weight': 1}]})
        self.assertEqual(len(cfg['prizes']), 2)
        patched = eng.normalize_config('LOTTERY', {'enabled': False}, cfg)
        self.assertFalse(patched['enabled'])
        self.assertEqual(patched['dailyLimit'], 5)
        self.assertEqual([p['name'] for p in patched['prizes']], ['A', 'B'])
        self.assertEqual(patched['prizes'][1]['type'], 'PRIZE')

    def test_patch_enabled_keeps_streak_rules(self):
        cfg = eng.normalize_config('CHECKIN', {
            'enabled': True, 'dailyPoints': 9, 'cycleDays': 14,
            'streakRules': [{'days': 7, 'points': 50}]})
        p = eng.normalize_config('CHECKIN', {'enabled': False}, cfg)
        self.assertEqual(p['dailyPoints'], 9)
        self.assertEqual(p['cycleDays'], 14)
        self.assertEqual(p['streakRules'][0]['points'], 50)

    def test_config_from_row(self):
        row = {'enabled': 1, 'config_json': eng.dumps({'dailyPoints': 12, 'cycleDays': 7,
                                                       'streakRules': []})}
        c = eng.config_from_row(row, 'CHECKIN')
        self.assertTrue(c['enabled'])
        self.assertEqual(c['dailyPoints'], 12)
        self.assertEqual(eng.config_from_row(None, 'CHECKIN')['enabled'], False)

    def test_loads_tolerates_garbage(self):
        self.assertEqual(eng.loads('not-json'), {})
        self.assertEqual(eng.loads(None), {})
        self.assertEqual(eng.loads('[1,2]'), {})


class DatabaseFlows(unittest.TestCase):
    def setUp(self):
        self.c = _db()

    def tearDown(self):
        self.c.close()

    def _points(self, club_id=1, uid=7):
        r = self.c.execute('SELECT club_points_balance FROM club_members WHERE club_id=? AND user_id=?',
                           (club_id, uid)).fetchone()
        return int(r[0]) if r else 0

    def test_get_config_missing_row_disabled(self):
        self.assertFalse(eng.get_config(self.c, 1, 'CHECKIN')['enabled'])

    def test_save_and_read_config_roundtrip(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 8,
                                     'streakRules': [{'days': 3, 'points': 20}]})
        cfg = eng.get_config(self.c, 1, 'CHECKIN')
        self.assertTrue(cfg['enabled'])
        self.assertEqual(cfg['dailyPoints'], 8)
        self.assertEqual(cfg['streakRules'][0]['points'], 20)
        _save(self.c, 1, 'CHECKIN', {'enabled': False, 'dailyPoints': 1, 'streakRules': []})
        self.assertFalse(eng.get_config(self.c, 1, 'CHECKIN')['enabled'])

    def test_checkin_disabled(self):
        self.assertEqual(eng.checkin(self.c, club_id=1, user_id=7)['reason'], 'disabled')

    def test_checkin_grants_real_points_and_ledger(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5})
        r = eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertTrue(r['ok'])
        self.assertEqual(r['pointsGranted'], 5)
        self.assertEqual(self._points(), 5)
        led = self.c.execute("SELECT * FROM club_point_ledger WHERE source_type='engagement'").fetchall()
        self.assertEqual(len(led), 1)
        self.assertEqual(led[0]['amount'], 5)

    def test_checkin_idempotent_same_day(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5})
        eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        dup = eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertEqual(dup['reason'], 'already')
        self.assertEqual(self._points(), 5)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM engagement_rewards').fetchone()[0], 1)

    def test_checkin_streak_bonus(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5, 'cycleDays': 7,
                                     'streakRules': [{'days': 3, 'points': 30}]})
        eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-11')
        eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-12')
        r = eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertEqual(r['streak'], 3)
        self.assertEqual(r['reward']['totalPoints'], 35)
        self.assertEqual(self._points(), 45)

    def test_checkin_club_isolated(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5})
        self.assertEqual(eng.checkin(self.c, club_id=2, user_id=7, day='2026-11-13')['reason'],
                         'disabled')
        self.assertEqual(self._points(club_id=2), 0)

    def test_draw_disabled(self):
        self.assertEqual(eng.draw(self.c, club_id=1, user_id=7)['reason'], 'disabled')

    def test_draw_grants_points_and_consumes_stock(self):
        _save(self.c, 1, 'LOTTERY', {
            'enabled': True, 'dailyLimit': 2,
            'prizes': [{'id': 'p1', 'name': '10积分', 'type': 'POINTS',
                        'points': 10, 'stock': 1, 'weight': 1}]})
        r1 = eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13', rnd=lambda: 0.0)
        self.assertTrue(r1['ok'])
        self.assertEqual(r1['prize']['name'], '10积分')
        self.assertEqual(self._points(), 10)
        r2 = eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13', rnd=lambda: 0.0)
        self.assertTrue(r2['ok'])
        self.assertIsNone(r2['prize'])          # 库存耗尽 → 谢谢参与
        self.assertEqual(r2['remaining'], 0)
        r3 = eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertEqual(r3['reason'], 'daily-limit')

    def test_draw_no_prize_still_logs_participation(self):
        _save(self.c, 1, 'LOTTERY', {'enabled': True, 'dailyLimit': 3, 'prizes': []})
        r = eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertTrue(r['ok'])
        self.assertIsNone(r['prize'])
        self.assertEqual(self._points(), 0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM engagement_logs').fetchone()[0], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM engagement_rewards').fetchone()[0], 0)

    def test_draw_prize_pending_fulfillment(self):
        _save(self.c, 1, 'LOTTERY', {
            'enabled': True, 'dailyLimit': 1,
            'prizes': [{'id': 'p1', 'name': '保温杯', 'type': 'PRIZE', 'weight': 1, 'stock': 5}]})
        r = eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13', rnd=lambda: 0.0)
        self.assertTrue(r['ok'])
        rr = self.c.execute('SELECT * FROM engagement_rewards').fetchone()
        self.assertEqual(rr['status'], 'PENDING')
        self.assertEqual(self._points(), 0)     # 实物奖品不自动加积分

    def test_stamp_bad_action_and_idempotent(self):
        _save(self.c, 1, 'STAMP', {'enabled': True, 'required': 3, 'rewardPoints': 50})
        self.assertEqual(eng.add_stamp(self.c, club_id=1, user_id=7,
                                       action='not-allowed', day='2026-11-13')['reason'], 'bad-action')
        a = eng.add_stamp(self.c, club_id=1, user_id=7, action='signup', day='2026-11-13')
        self.assertTrue(a['ok'])
        self.assertEqual(a['collected'], 1)
        dup = eng.add_stamp(self.c, club_id=1, user_id=7, action='signup', day='2026-11-13')
        self.assertEqual(dup['reason'], 'already')

    def test_stamp_redeem_once_and_cycle_restart(self):
        _save(self.c, 1, 'STAMP', {'enabled': True, 'required': 2, 'rewardPoints': 50})
        eng.add_stamp(self.c, club_id=1, user_id=7, action='signup', day='2026-11-13')
        r = eng.add_stamp(self.c, club_id=1, user_id=7, action='share', day='2026-11-13')
        self.assertTrue(r['redeemed'])
        self.assertEqual(r['pointsGranted'], 50)
        self.assertEqual(r['collected'], 0)
        self.assertEqual(self._points(), 50)
        # 再来两枚 → 应触发第二个周期的兑换，且不会重复发第一次
        eng.add_stamp(self.c, club_id=1, user_id=7, action='signup', day='2026-11-14')
        r2 = eng.add_stamp(self.c, club_id=1, user_id=7, action='review', day='2026-11-14')
        self.assertTrue(r2['redeemed'])
        self.assertEqual(self._points(), 100)
        self.assertEqual(self.c.execute(
            "SELECT COUNT(*) FROM engagement_rewards WHERE kind='STAMP'").fetchone()[0], 2)

    def test_grant_source_key_idempotent(self):
        g1 = eng.grant(self.c, club_id=1, user_id=7, kind='CHECKIN', points=8, source_key='K1')
        g2 = eng.grant(self.c, club_id=1, user_id=7, kind='CHECKIN', points=8, source_key='K1')
        self.assertTrue(g1['ok'])
        self.assertEqual(g2['reason'], 'duplicated')
        self.assertEqual(self._points(), 8)

    def test_user_status_shapes(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5})
        _save(self.c, 1, 'LOTTERY', {'enabled': True, 'dailyLimit': 2, 'prizes': []})
        _save(self.c, 1, 'STAMP', {'enabled': True, 'required': 2, 'rewardPoints': 50})
        s = eng.user_status(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertFalse(s['CHECKIN']['signedToday'])
        self.assertEqual(s['CHECKIN']['todayReward']['totalPoints'], 5)
        self.assertEqual(s['LOTTERY']['remaining'], 2)
        self.assertEqual(s['STAMP']['collected'], 0)
        eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        eng.add_stamp(self.c, club_id=1, user_id=7, action='signup', day='2026-11-13')
        s2 = eng.user_status(self.c, club_id=1, user_id=7, day='2026-11-13')
        self.assertTrue(s2['CHECKIN']['signedToday'])
        self.assertEqual(s2['CHECKIN']['streak'], 1)
        self.assertEqual(s2['STAMP']['collected'], 1)

    def test_summary_counts(self):
        _save(self.c, 1, 'CHECKIN', {'enabled': True, 'dailyPoints': 5})
        _save(self.c, 1, 'LOTTERY', {'enabled': True, 'dailyLimit': 3, 'prizes': []})
        eng.checkin(self.c, club_id=1, user_id=7, day='2026-11-13')
        eng.checkin(self.c, club_id=1, user_id=8, day='2026-11-13')
        eng.draw(self.c, club_id=1, user_id=7, day='2026-11-13')
        s = eng.summary(self.c, 1)
        self.assertEqual(s['checkins'], 2)
        self.assertEqual(s['draws'], 1)
        self.assertEqual(s['participants'], 2)
        self.assertEqual(s['pointsGranted'], 10)


if __name__ == '__main__':
    unittest.main()
