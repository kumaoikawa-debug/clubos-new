"""stores 实体单元测试：归一化、校验、坐标/营业时间裁剪、落库往返。"""
import importlib.util, os, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'stores.py')
    spec = importlib.util.spec_from_file_location('stores_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


st = _load()


class TestNormalizeStore(unittest.TestCase):
    def test_minimal(self):
        n = st.normalize_store({'name': '城西基地'})
        self.assertEqual(n['name'], '城西基地')
        self.assertEqual(n['status'], 'ACTIVE')

    def test_name_required(self):
        with self.assertRaises(ValueError):
            st.normalize_store({'address': 'no name'})

    def test_geo_valid(self):
        n = st.normalize_store({'name': 'x', 'geo_json': {'lat': 30.57, 'lng': 104.06, 'label': '总部'}})
        self.assertEqual(n['geo_json']['lat'], 30.57)
        self.assertEqual(n['geo_json']['label'], '总部')

    def test_geo_invalid_dropped(self):
        n = st.normalize_store({'name': 'x', 'geo_json': {'lat': 999, 'lng': 104}})
        self.assertIsNone(n['geo_json'])

    def test_geo_non_numeric_dropped(self):
        n = st.normalize_store({'name': 'x', 'geo_json': {'lat': 'abc', 'lng': 'def'}})
        self.assertIsNone(n['geo_json'])

    def test_hours_clamped(self):
        n = st.normalize_store({'name': 'x', 'hours_json': [
            {'day': 0, 'open': '09:00', 'close': '18:00'},
            {'day': 9, 'open': '09:00'},  # 非法 day → 丢弃
            {'day': 1, 'closed': True},
        ]})
        self.assertEqual(len(n['hours_json']), 2)
        self.assertEqual(n['hours_json'][0]['day'], 0)

    def test_status_normalized(self):
        n = st.normalize_store({'name': 'x', 'status': 'weird'})
        self.assertEqual(n['status'], 'ACTIVE')
        n2 = st.normalize_store({'name': 'x', 'status': 'inactive'})
        self.assertEqual(n2['status'], 'INACTIVE')

    def test_truncation(self):
        n = st.normalize_store({'name': 'x' * 100})
        self.assertEqual(len(n['name']), 40)
        n2 = st.normalize_store({'name': 'x', 'address': 'a' * 300})
        self.assertEqual(len(n2['address']), 120)

    def test_merge_current(self):
        cur = {'name': '旧名', 'address': '旧地址'}
        n = st.normalize_store({'address': '新地址'}, cur)
        self.assertEqual(n['name'], '旧名')  # 未提供 name → 沿用 current
        self.assertEqual(n['address'], '新地址')


class TestJson(unittest.TestCase):
    def test_roundtrip(self):
        g = {'lat': 30.0, 'lng': 104.0}
        self.assertEqual(st.loads(st.dumps(g)), g)
        self.assertEqual(st.loads(''), {})
        self.assertEqual(st.loads('not json'), {})


if __name__ == '__main__':
    unittest.main()
