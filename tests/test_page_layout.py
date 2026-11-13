"""page_layout 引擎单元测试：归一化、校验、公开渲染、媒体代理、落库往返。"""
import importlib.util, os, unittest


def _load():
    p = os.path.join(os.path.dirname(__file__), '..', 'clubos_domain', 'page_layout.py')
    spec = importlib.util.spec_from_file_location('page_layout_test_mod', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pl = _load()


class TestNormalizeLayout(unittest.TestCase):
    def test_valid_full(self):
        payload = {
            'page_key': 'club_home', 'title': '主页', 'status': 'PUBLISHED',
            'components': [
                {'type': 'carousel', 'props': {'items': [
                    {'image': '/static/uploads/1/home/a.jpg', 'title': 'A', 'targetType': 'activity', 'targetId': 5},
                    {'image': 'https://x.com/b.png'},  # 无图 target 型 → 纯展示
                ]}},
                {'type': 'notice', 'props': {'items': [{'text': '公告一', 'link': '/p/1'}]}},
                {'type': 'free_container', 'props': {'title': 'T', 'content': 'C', 'image': '/static/uploads/1/home/c.jpg'}},
            ],
        }
        norm = pl.normalize_layout(payload)
        self.assertEqual(norm['page_key'], 'club_home')
        self.assertEqual(norm['status'], 'PUBLISHED')
        self.assertEqual(len(norm['components']), 3)
        # carousel 两个 item 都保留（第二张无 target 降级纯展示）
        self.assertEqual(len(norm['components'][0]['props']['items']), 2)
        self.assertEqual(norm['components'][1]['props']['items'][0]['text'], '公告一')

    def test_bad_page_key(self):
        with self.assertRaises(ValueError):
            pl.normalize_layout({'page_key': 'foo', 'components': []})

    def test_bad_status_falls_back(self):
        norm = pl.normalize_layout({'page_key': 'activity_detail', 'status': 'WEIRD', 'components': []})
        self.assertEqual(norm['status'], 'DRAFT')

    def test_drop_empty_carousel(self):
        # 轮播没图 item → 整组件丢弃
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'carousel', 'props': {'items': [{'title': 'no image'}]}},
        ]})
        self.assertEqual(len(norm['components']), 0)

    def test_unknown_component_dropped(self):
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'totally_unknown', 'props': {}},
            {'type': 'notice', 'props': {'text': 'hi'}},
        ]})
        self.assertEqual(len(norm['components']), 1)
        self.assertEqual(norm['components'][0]['type'], 'notice')

    def test_nav_grid_emoji_icon_allowed(self):
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'nav_grid', 'props': {'items': [{'label': '会员', 'icon': '⭐', 'targetType': 'marketing'}]}},
        ]})
        self.assertEqual(norm['components'][0]['props']['items'][0]['icon'], '⭐')

    def test_activity_list_filter(self):
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'activity_list', 'props': {'title': '热门', 'filter_json': {'kind': 'hiking', 'limit': 99}}},
        ]})
        f = norm['components'][0]['props']['filter_json']
        self.assertEqual(f['limit'], 12)  # 99 被夹到 MAX_ITEMS
        self.assertEqual(f['kind'], 'hiking')

    def test_target_without_id_downgrades(self):
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'carousel', 'props': {'items': [{'image': '/static/x.jpg', 'targetType': 'activity', 'targetId': 'bad'}]}},
        ]})
        it = norm['components'][0]['props']['items'][0]
        self.assertEqual(it['targetType'], '')
        self.assertIsNone(it['targetId'])


class TestRenderAndMedia(unittest.TestCase):
    def test_render_public_proxies_images(self):
        schema = pl.dumps(pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'carousel', 'props': {'items': [{'image': '/static/uploads/1/home/a.jpg'}]}},
            {'type': 'free_container', 'props': {'title': 'T', 'image': '/static/uploads/1/home/c.jpg'}},
        ]})['components'])
        rendered = pl.render_public(1, schema)
        self.assertIsNotNone(rendered)
        # 图片应被改写成公开代理地址
        self.assertTrue(rendered[0]['props']['items'][0]['image'].startswith('/api/public/clubs/1/page-media/'))
        # http(s) 不变
        schema2 = pl.dumps([{'type': 'carousel', 'props': {'items': [{'image': 'https://x.com/b.png'}]}}])
        r2 = pl.render_public(1, schema2)
        self.assertEqual(r2[0]['props']['items'][0]['image'], 'https://x.com/b.png')

    def test_render_none_when_empty(self):
        self.assertIsNone(pl.render_public(1, pl.dumps([])))
        self.assertIsNone(pl.render_public(1, None))

    def test_media_proxy_url(self):
        self.assertEqual(pl.media_proxy_url(7, '/static/uploads/7/h.jpg'),
                         '/api/public/clubs/7/page-media/uploads/7/h.jpg')
        self.assertEqual(pl.media_proxy_url(7, 'https://x.com/y.png'), 'https://x.com/y.png')

    def test_media_paths_set(self):
        schema = pl.dumps(pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'carousel', 'props': {'items': [{'image': '/static/a.jpg'}, {'image': 'https://b.png'}]}},
            {'type': 'nav_grid', 'props': {'items': [{'label': 'L', 'icon': '/static/ic.png'}]}},
        ]})['components'])
        paths = pl.media_paths(schema)
        self.assertIn('/static/a.jpg', paths)
        self.assertIn('/static/ic.png', paths)
        self.assertNotIn('https://b.png', paths)

    def test_roundtrip_dumps_loads(self):
        norm = pl.normalize_layout({'page_key': 'club_home', 'components': [
            {'type': 'notice', 'props': {'items': [{'text': 'x', 'link': '/l'}]}},
        ]})
        blob = pl.dumps(norm['components'])
        back = pl.loads(blob)
        self.assertEqual(back[0]['props']['items'][0]['text'], 'x')


if __name__ == '__main__':
    unittest.main()
