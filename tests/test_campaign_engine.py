"""统一营销活动引擎单元测试（P0-1）。

直接 importlib 加载 clubos_domain/campaign_engine.py，绕开 clubos_domain 包 __init__
的重导入，保证可独立验证、不依赖完整 app 启动。
"""
import importlib.util
import os
import sqlite3
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_PATH = os.path.join(_HERE, '..', 'clubos_domain', 'campaign_engine.py')

_spec = importlib.util.spec_from_file_location('campaign_engine_test', _ENGINE_PATH)
_engine_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_engine_mod)
CampaignEngine = _engine_mod.CampaignEngine
validate_campaign_payload = _engine_mod.validate_campaign_payload

SCHEMA = """
CREATE TABLE marketing_campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT NOT NULL DEFAULT 'CLUB',
  owner_club_id INTEGER,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  schedule_json TEXT, scope_json TEXT, rules_json TEXT, stats_json TEXT,
  created_by INTEGER, created_at TEXT, updated_at TEXT
);
CREATE TABLE registrations (club_id INTEGER, user_id INTEGER, status TEXT);
"""


def new_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def add_campaign(c, **kw):
    c.execute(
        """INSERT INTO marketing_campaigns
           (owner_type, owner_club_id, type, title, status, schedule_json, scope_json, rules_json)
           VALUES(?,?,?,?,?,?,?,?)""",
        (kw.get('owner_type', 'CLUB'), kw.get('owner_club_id'),
         kw['type'], kw['title'], kw.get('status', 'ACTIVE'),
         kw.get('schedule_json', '{}'), kw.get('scope_json', '{}'), kw.get('rules_json', '{}')))
    return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])


class CampaignEngineTest(unittest.TestCase):

    def test_newcomer_first_order_discount(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='NEWCOMER', title='首单立减30',
                     rules_json='{"mode":"fixed","amount":30,"maxDiscount":50}')
        eng = CampaignEngine()
        # 首单用户（无 registrations 记录）→ 应减 30
        r = eng.evaluate(c, club_id=1, user_id=101, payable=500.0, activity_ids=[10])
        self.assertAlmostEqual(r['discount'], 30.0)
        self.assertEqual(r['applied'][0]['type'], 'NEWCOMER')
        # 非首单用户（已有支付记录）→ 不应减
        c.execute("INSERT INTO registrations VALUES(1,102,'paid')")
        r2 = eng.evaluate(c, club_id=1, user_id=102, payable=500.0)
        self.assertAlmostEqual(r2['discount'], 0.0)

    def test_timelimit_percent_within_window(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='TIME_LIMIT', title='限时9折',
                     rules_json='{"mode":"percent","percent":10}',
                     schedule_json='{"startAt":1,"endAt":9999999999}')
        r = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=200.0)
        self.assertAlmostEqual(r['discount'], 20.0)

    def test_timelimit_out_of_window(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='TIME_LIMIT', title='过期活动',
                     rules_json='{"mode":"fixed","amount":20}',
                     schedule_json='{"endAt":100}')
        r = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=200.0)
        self.assertAlmostEqual(r['discount'], 0.0)

    def test_scope_mismatch_skips(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='TIME_LIMIT', title='指定活动',
                     rules_json='{"mode":"fixed","amount":15}',
                     scope_json='{"activityIds":[99]}')
        # 订单活动不在 scope 内 → 跳过
        r = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=200.0, activity_ids=[10])
        self.assertAlmostEqual(r['discount'], 0.0)
        # 命中 → 生效
        r2 = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=200.0, activity_ids=[99])
        self.assertAlmostEqual(r2['discount'], 15.0)

    def test_platform_campaign_applies_to_all_clubs(self):
        c = new_db()
        add_campaign(c, owner_type='PLATFORM', owner_club_id=None, type='TIME_LIMIT',
                     title='平台全场减10', rules_json='{"mode":"fixed","amount":10}')
        r = CampaignEngine().evaluate(c, club_id=999, user_id=5, payable=100.0)
        self.assertAlmostEqual(r['discount'], 10.0)

    def test_discount_capped_at_payable(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='TIME_LIMIT', title='大额减',
                     rules_json='{"mode":"fixed","amount":999}')
        r = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=50.0)
        self.assertAlmostEqual(r['discount'], 50.0)

    def test_non_active_skipped(self):
        c = new_db()
        add_campaign(c, owner_club_id=1, type='TIME_LIMIT', title='草稿',
                     status='DRAFT', rules_json='{"mode":"fixed","amount":10}')
        r = CampaignEngine().evaluate(c, club_id=1, user_id=5, payable=100.0)
        self.assertAlmostEqual(r['discount'], 0.0)

    def test_validate_rejects_bad_type(self):
        with self.assertRaises(ValueError):
            validate_campaign_payload({'type': 'NOPE', 'title': 'x', 'rules': {'mode': 'fixed', 'amount': 1}})

    def test_validate_rejects_bad_percent(self):
        with self.assertRaises(ValueError):
            validate_campaign_payload({'type': 'TIME_LIMIT', 'title': 'x', 'rules': {'mode': 'percent', 'percent': 150}})

    def test_validate_ok(self):
        p = validate_campaign_payload({'type': 'NEWCOMER', 'title': '首单', 'rules': {'mode': 'fixed', 'amount': 30}})
        self.assertEqual(p['type'], 'NEWCOMER')
        self.assertEqual(p['status'], 'DRAFT')

    def test_crud_roundtrip(self):
        c = new_db()
        eng = CampaignEngine()
        created = eng.create_campaign(c, owner_type='CLUB', owner_club_id=1,
                                     payload={'type': 'TIME_LIMIT', 'title': '限时', 'rules': {'mode': 'fixed', 'amount': 12}})
        cid = created['id']
        self.assertEqual(created['status'], 'DRAFT')
        eng.update_campaign(c, campaign_id=cid, payload={'status': 'ACTIVE'})
        got = eng.get_campaign(c, cid)
        self.assertEqual(got['status'], 'ACTIVE')
        self.assertEqual(len(eng.list_campaigns(c, owner_club_id=1)), 1)
        eng.delete_campaign(c, campaign_id=cid, owner_club_id=1)
        self.assertEqual(eng.get_campaign(c, cid)['status'], 'ENDED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
