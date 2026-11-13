"""P1-3 拼团 domain 单测（纯函数 + 内存 sqlite 状态机）。

用 importlib 直接加载 clubos_domain/group_buy.py，避免触发包级 __init__ 的重依赖。
"""
import importlib.util
import os
import sqlite3
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'clubos_domain', 'group_buy.py')
_spec = importlib.util.spec_from_file_location('group_buy_under_test', _PATH)
gb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gb)

DDL = """
CREATE TABLE group_buy_campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, club_id INTEGER NOT NULL, activity_id INTEGER,
  title TEXT NOT NULL, required_people INTEGER NOT NULL DEFAULT 3,
  group_price_cents INTEGER NOT NULL, original_price_cents INTEGER NOT NULL DEFAULT 0,
  duration_hours INTEGER NOT NULL DEFAULT 24, max_teams INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'DRAFT', start_at INTEGER, end_at INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE TABLE group_buy_teams (
  id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, club_id INTEGER NOT NULL,
  activity_id INTEGER, leader_user_id INTEGER NOT NULL, required_people INTEGER NOT NULL DEFAULT 3,
  price_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
  member_count INTEGER NOT NULL DEFAULT 1, expire_at INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE TABLE group_buy_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INTEGER NOT NULL, club_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'MEMBER',
  price_cents INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'JOINED',
  joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(team_id,user_id));
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, phone TEXT);
"""

BASE = {'title': '雪山徒步 3 人团', 'activityId': 11, 'requiredPeople': 3,
        'groupPrice': 299, 'originalPrice': 399, 'durationHours': 24,
        'status': 'ACTIVE', 'maxTeams': 0}


def _db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    for u in range(1, 7):
        c.execute('INSERT INTO users(id,name,phone) VALUES(?,?,?)', (u, 'u%d' % u, '1380000000%d' % u))
    return c


def _camp(c, **kw):
    p = dict(BASE)
    p.update(kw)
    return gb.create_campaign(c, 1, p)


class PureFunctions(unittest.TestCase):
    def test_cents_roundtrip(self):
        self.assertEqual(gb.to_cents(299), 29900)
        self.assertEqual(gb.from_cents(29900), 299.0)
        self.assertEqual(gb.to_cents('bad'), 0)
        self.assertEqual(gb.to_cents(-1), 0)

    def test_normalize_campaign(self):
        n = gb.normalize_campaign(BASE)
        self.assertEqual(n['groupPriceCents'], 29900)
        self.assertEqual(n['originalPriceCents'], 39900)
        self.assertEqual(n['requiredPeople'], 3)
        self.assertEqual(n['status'], 'ACTIVE')

    def test_validate_title_price(self):
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, title=''))
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, groupPrice=0))
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, originalPrice=100))

    def test_validate_people_not_clamped(self):
        """1 人成团必须报错，不能被 _int 夹逼悄悄洗成 2 人。"""
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, requiredPeople=1))
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, requiredPeople=999))

    def test_validate_status_and_window(self):
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, status='BOGUS'))
        with self.assertRaises(ValueError):
            gb.normalize_campaign(dict(BASE, startAt=200, endAt=100))

    def test_patch_status_keeps_prices(self):
        """P1-1 单位坑回归：current 只有 Cents 字段时不得把「分」当「元」放大 100 倍。"""
        current = {'title': '老配置', 'activityId': 11, 'requiredPeople': 3,
                   'groupPriceCents': 29900, 'originalPriceCents': 39900,
                   'durationHours': 24, 'maxTeams': 0, 'status': 'ACTIVE'}
        n = gb.normalize_campaign({'status': 'PAUSED'}, current)
        self.assertEqual(n['groupPriceCents'], 29900)
        self.assertEqual(n['originalPriceCents'], 39900)
        self.assertEqual(n['status'], 'PAUSED')

    def test_window_ok(self):
        cfg = {'startAt': 100, 'endAt': 200}
        self.assertFalse(gb.window_ok(cfg, 99))
        self.assertTrue(gb.window_ok(cfg, 150))
        self.assertFalse(gb.window_ok(cfg, 201))
        self.assertTrue(gb.window_ok({}, 999))

    def test_team_active(self):
        self.assertTrue(gb.team_active({'status': 'OPEN', 'expire_at': 200}, 100))
        self.assertFalse(gb.team_active({'status': 'OPEN', 'expire_at': 100}, 100))
        self.assertFalse(gb.team_active({'status': 'SUCCESS', 'expire_at': 999}, 100))

    def test_views(self):
        cv = gb.campaign_view({'id': 1, 'club_id': 1, 'activity_id': 11,
                               'group_price_cents': 29900, 'original_price_cents': 39900})
        self.assertEqual(cv['groupPrice'], 299.0)
        tv = gb.team_view({'id': 2, 'required_people': 3, 'member_count': 1, 'price_cents': 29900})
        self.assertEqual(tv['remaining'], 2)
        self.assertEqual(tv['price'], 299.0)

    def test_loads_tolerates_garbage(self):
        self.assertEqual(gb.loads('x'), {})
        self.assertEqual(gb.loads(None), {})


class DatabaseFlows(unittest.TestCase):
    def setUp(self):
        self.c = _db()

    def tearDown(self):
        self.c.close()

    def test_campaign_crud(self):
        cp = _camp(self.c)
        self.assertEqual(cp['groupPriceCents'], 29900)
        self.assertEqual(len(gb.list_campaigns(self.c, 1)), 1)
        up = gb.update_campaign(self.c, 1, cp['id'], {'status': 'PAUSED'})
        self.assertEqual(up['status'], 'PAUSED')
        self.assertEqual(up['groupPriceCents'], 29900)   # PATCH 不丢价格
        gb.delete_campaign(self.c, 1, cp['id'])
        self.assertEqual(gb.list_campaigns(self.c, 1), [])
        with self.assertRaises(LookupError):
            gb.get_campaign(self.c, 2, cp['id'])

    def test_open_team_requires_active(self):
        cp = _camp(self.c, status='DRAFT')
        r = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        self.assertEqual(r['reason'], 'campaign-not-active')

    def test_open_team_creates_leader_member(self):
        cp = _camp(self.c)
        r = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        self.assertTrue(r['ok'])
        self.assertEqual(r['team']['memberCount'], 1)
        self.assertEqual(r['team']['remaining'], 2)
        m = self.c.execute('SELECT * FROM group_buy_members WHERE team_id=?', (r['teamId'],)).fetchall()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]['role'], 'LEADER')
        self.assertEqual(m[0]['price_cents'], 29900)

    def test_cannot_open_two_teams_same_campaign(self):
        cp = _camp(self.c)
        gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        r = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        self.assertEqual(r['reason'], 'already-in-team')

    def test_max_teams(self):
        cp = _camp(self.c, maxTeams=1)
        gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        r = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=2)
        self.assertEqual(r['reason'], 'max-teams')

    def test_window_blocks_open(self):
        cp = _camp(self.c, endAt=1)
        r = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)
        self.assertEqual(r['reason'], 'out-of-window')

    def test_join_until_success(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        j1 = gb.join_team(self.c, club_id=1, team_id=t, user_id=2)
        self.assertTrue(j1['ok']) and self.assertFalse(j1['isSuccess'])
        self.assertEqual(j1['team']['memberCount'], 2)
        dup = gb.join_team(self.c, club_id=1, team_id=t, user_id=2)
        self.assertEqual(dup['reason'], 'already-joined')
        j2 = gb.join_team(self.c, club_id=1, team_id=t, user_id=3)
        self.assertTrue(j2['isSuccess'])
        self.assertEqual(j2['team']['status'], 'SUCCESS')
        self.assertEqual(len(j2['members']), 3)
        self.assertTrue(all(m['status'] == 'SUCCESS' for m in j2['members']))
        later = gb.join_team(self.c, club_id=1, team_id=t, user_id=4)
        self.assertEqual(later['reason'], 'already-success')

    def test_join_missing_team(self):
        self.assertEqual(gb.join_team(self.c, club_id=1, team_id=999, user_id=1)['reason'],
                         'team-not-found')

    def test_cannot_join_two_open_teams_same_campaign(self):
        cp = _camp(self.c)
        t1 = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        t2 = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=2)['teamId']
        r = gb.join_team(self.c, club_id=1, team_id=t1, user_id=2)
        self.assertEqual(r['reason'], 'already-in-team')
        self.assertEqual(gb.team_detail(self.c, 1, t2)['memberCount'], 1)

    def test_expire_due_is_lazy(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        self.c.execute('UPDATE group_buy_teams SET expire_at=? WHERE id=?', (gb.now_ts() - 1, t))
        self.assertEqual(gb.expire_due(self.c, 1), 1)
        self.assertEqual(gb.team_detail(self.c, 1, t)['status'], 'EXPIRED')
        self.assertEqual(gb.join_team(self.c, club_id=1, team_id=t, user_id=2)['reason'],
                         'team-expired')

    def test_cancel_team_permissions(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        self.assertEqual(gb.cancel_team(self.c, club_id=1, team_id=t, user_id=9)['reason'],
                         'not-leader')
        self.assertTrue(gb.cancel_team(self.c, club_id=1, team_id=t, user_id=1)['ok'])
        self.assertEqual(gb.team_detail(self.c, 1, t)['status'], 'CANCELLED')
        self.assertEqual(gb.join_team(self.c, club_id=1, team_id=t, user_id=2)['reason'],
                         'team-closed')
        self.assertEqual(gb.cancel_team(self.c, club_id=1, team_id=t, by_admin=True)['reason'],
                         'team-not-open')

    def test_team_lists_and_summary(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        gb.join_team(self.c, club_id=1, team_id=t, user_id=2)
        self.assertEqual(len(gb.list_teams(self.c, 1)), 1)
        self.assertEqual(len(gb.list_teams(self.c, 1, status='OPEN')), 1)
        self.assertEqual(len(gb.user_teams(self.c, 1, 2)), 1)
        self.assertEqual(len(gb.team_detail(self.c, 1, t)['members']), 2)
        s = gb.summary(self.c, 1)
        self.assertEqual(s['campaigns'], 1)
        self.assertEqual(s['teams'], 1)
        self.assertEqual(s['open'], 1)
        self.assertEqual(s['members'], 2)

    def test_quote_for_team_readonly(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        q = gb.quote_for_team(self.c, club_id=1, activity_id=cp['activityId'],
                              team_id=t, user_id=1)
        self.assertEqual(q['price'], 299.0)
        self.assertEqual(q['originalPrice'], 399.0)
        self.assertEqual(q['saving'], 100.0)
        self.assertFalse(q['appliedToPayable'])
        self.assertEqual(q['remaining'], 2)
        self.assertIsNone(gb.quote_for_team(self.c, club_id=1, activity_id=999,
                                            team_id=t, user_id=1))
        self.assertIsNone(gb.quote_for_team(self.c, club_id=1, activity_id=cp['activityId'],
                                            team_id=t, user_id=6))
        # 纯读：不得写入任何状态
        self.assertEqual(self.c.execute(
            "SELECT status FROM group_buy_teams WHERE id=?", (t,)).fetchone()[0], 'OPEN')

    def test_club_isolated(self):
        cp = _camp(self.c)
        t = gb.open_team(self.c, club_id=1, campaign_id=cp['id'], user_id=1)['teamId']
        self.assertEqual(gb.open_team(self.c, club_id=2, campaign_id=cp['id'], user_id=1)['reason'],
                         'campaign-not-found')
        self.assertEqual(gb.join_team(self.c, club_id=2, team_id=t, user_id=2)['reason'],
                         'team-not-found')
        self.assertIsNone(gb.team_detail(self.c, 2, t))


if __name__ == '__main__':
    unittest.main()
