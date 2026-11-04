"""长图风格、真实照片与模型/兜底路径回归；不联网、不读写业务数据库。"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_engine as A
import longpic as L
import longpic_template as T
from outdoor_longpic import STYLE_PROMPT, apply_style, photos_valid


def model_html(refs, text='记录中的真实活动。' * 60):
    return '<style>section{padding:48px}</style>' + ''.join(
        '<img src="{{media:%s}}">' % r for r in refs) + '<h1>山野记录</h1><p>' + text + '</p>'


class OutdoorLongpicTest(unittest.TestCase):
    def test_one_style_two_different_layouts(self):
        a = apply_style(model_html(['img_01']))
        b = apply_style('<section style="display:flex"><div>文案</div><img src="{{media:img_01}}"></section>')
        for html in (a, b):
            self.assertIn('id="clubos-outdoor"', html)
            self.assertIn('#F4F1E8', html)
            self.assertIn('#29483B', html)
        self.assertNotEqual(a, b)
        self.assertIn('display:flex', b)

    def test_photo_whitelist_and_coverage(self):
        self.assertTrue(photos_valid(model_html(['rcap_01']), {'rcap_01'}, 1))
        self.assertFalse(photos_valid(model_html(['rcap_999']), {'rcap_01'}, 1))
        self.assertFalse(photos_valid(model_html(['rcap_01'] * 2), {'rcap_01', 'rcap_02'}, 2))
        self.assertFalse(photos_valid('<img src="https://example.invalid/a.jpg">', {'rcap_01'}, 1))
        self.assertTrue(photos_valid('<p>真实文字记录</p>', set(), 0))

    def test_template_and_mock_share_style(self):
        for html in (T.render({'hero': {'title': '山野'}, 'sections': [], 'signup': {}}),
                     L._mock({'title': '山野'}, {}, None)['html']):
            self.assertIn('id="clubos-outdoor"', html)

    def test_promo_model_keeps_layout_and_facts(self):
        async def fake(**kwargs):
            self.assertIn(STYLE_PROMPT, kwargs['system_prompt'])
            return SimpleNamespace(data={'title': '林间步道', 'html': model_html(['img_01', 'img_02'])})
        with patch.object(L, 'generate_json', fake):
            data, _ = asyncio.run(L.generate_longpic(1, {'title': '林间步道'}, {},
                    caption_lines=['img_01｜森林', 'img_02｜湖泊']))
        self.assertEqual(data['route'], 'model-html')
        self.assertIn('id="clubos-outdoor"', data['html'])
        self.assertEqual(set(data['usedRefs']), {'img_01', 'img_02'})

    def test_poster_only_uses_reference_style_families(self):
        data = A.normalize_poster({'headline': '山野', 'posterFamily': 'cosmic'})
        self.assertEqual(data['posterFamily'], 'film')
        self.assertEqual(data['style'], 'outdoor-minimal-v1')

    def test_recap_rejects_unknown_and_restores_all_photos(self):
        async def check(refs):
            async def fake(**kwargs):
                self.assertIn(STYLE_PROMPT, kwargs['system_prompt'])
                return SimpleNamespace(data={'html': model_html(refs)})
            with patch.object(A, 'generate_json', fake):
                return await A.generate_recap_html(1, {'title': '山野'}, '领队的现场记录',
                    [{'ref': 'rcap_%02d' % i} for i in range(1, 5)])
        self.assertIsNone(asyncio.run(check(['rcap_999']))[0])
        self.assertTrue(photos_valid(asyncio.run(check(['rcap_01']))[0], {'rcap_%02d' % i for i in range(1,5)}, 4))
        html = asyncio.run(check(['rcap_01', 'rcap_02']))[0]
        self.assertIn('id="clubos-outdoor"', html)

    def test_recap_offline_is_honest_and_styled(self):
        async def fake(**kwargs):
            if kwargs['system_prompt'] == A.SYSTEM:
                self.assertNotIn(A._WRITING_CONTRACT, kwargs['user_prompt'])
            return None
        usage = SimpleNamespace(usage_id=0)
        with patch.object(A, 'generate_json', fake), patch.object(A, 'record_mock_usage', return_value=usage):
            data, _ = asyncio.run(A.generate_recap(1, {'title': '山野'}, '大家走完了记录中的路线。',
                                     [{'ref': 'rcap_01', 'url': '/static/uploads/1/a.jpg'}], layout_seed='new-layout'))
        self.assertEqual(data['route'], 'offline')
        self.assertEqual(data['seed'], 'new-layout')
        self.assertEqual(data['facts']['title'], '山野')
        self.assertIn('id="clubos-outdoor"', data['html'])
        self.assertNotIn('扫码', data['html'])
        self.assertNotIn('报名', data['html'])


if __name__ == '__main__':
    unittest.main()
