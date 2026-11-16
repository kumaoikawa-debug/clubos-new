"""营地详情 AI · 章节叙事长图模板（story）测试。

学习用户提供的「流萤花谷」式营地介绍长图：引言 → 初见 → 安营 → 玩乐 →
烟火 → 相伴 → 出行须知。验证模板库 / prompt / 本地兜底 / 归一化全链路。
"""
import asyncio
import unittest

from clubos_domain import camp_ai_kit as kit
from clubos_domain import camp_detail_ai as dai


class StoryTemplateTest(unittest.TestCase):

    def test_story_template_in_registry(self):
        ids = [t['id'] for t in kit.list_templates()]
        self.assertIn('story', ids)
        self.assertEqual(set(ids), {'classic', 'magazine', 'spec', 'story'})
        t = kit.get_template('story')
        self.assertEqual(t['name'], '营地叙事长图')
        self.assertEqual(len(t['blueprint']), 7)

    def test_story_blueprint_headlines_and_layouts(self):
        bp = kit.get_template('story')['blueprint']
        heads = [b['headline'] for b in bp]
        self.assertEqual(heads, ['写在前面', '初见营地', '安营扎寨',
                                 '逐水而行', '围炉烟火', '相伴时光', '出行须知'])
        self.assertEqual([b['layout'] for b in bp],
                         ['full', 'full', 'split', 'grid', 'grid', 'split', 'text'])
        self.assertEqual(bp[-1]['maxImages'], 0)
        self.assertTrue(all(b['layout'] in kit.LAYOUTS for b in bp))

    def test_story_prompt_has_narrative_guidance(self):
        p = kit.template_prompt('story')
        self.assertIn('营地叙事长图', p)
        self.assertTrue('场景化' in p or '章节叙事' in p)
        self.assertIn('写在前面', p)
        self.assertIn('不编造', p)
        self.assertNotIn('杂志式章节叙事口吻', kit.template_prompt('classic'))

    def test_apply_blueprint_caps_images_for_story(self):
        secs = [{'headline': h, 'mediaRefs': list(range(12))}
                for h in ['写在前面', '初见营地', '安营扎寨', '逐水而行',
                          '围炉烟火', '相伴时光', '出行须知']]
        out = kit.apply_blueprint(secs, 'story', media_count=12)
        self.assertEqual(len(out), 7)
        self.assertEqual(len(out[0]['mediaRefs']), 2)
        self.assertEqual(len(out[3]['mediaRefs']), 3)
        self.assertEqual(out[-1]['mediaRefs'], [])
        self.assertEqual(out[-1]['layout'], 'text')

    def test_local_story_detail_is_narrative_not_rules(self):
        media = [
            {'url': '/uploads/a.jpg', 'source': 'gallery', 'caption': '开阔湖景，蓝天与水面',
             'tags': ['天空', '湖景'], 'usable': True},
            {'url': '/uploads/b.jpg', 'source': 'gallery', 'caption': '木构营位与暖灯',
             'tags': ['木构', '营地设施'], 'usable': True},
        ]
        base = {'name': '流萤花谷', 'hardSpecs': {'可住人数': '4 位'}}
        source = '退改规则：提前三天可取消。营位配电源插口，24 小时热水。'
        out = dai._local_detail(base=base, source_text=source, media=media, template_id='story')
        self.assertEqual(out['templateId'], 'story')
        self.assertEqual(len(out['sections']), 7)
        heads = [s['headline'] for s in out['sections']]
        self.assertEqual(heads[-1], '出行须知')
        bodies = [s['body'] for s in out['sections']]
        self.assertTrue('城市' in bodies[0] or '自然' in bodies[0])
        self.assertNotIn('退改规则', bodies[0])
        self.assertTrue(any(k in bodies[-1] for k in ('退改', '预订', '规则')))
        self.assertTrue(any(s['mediaRefs'] for s in out['sections'][:-1]))
        self.assertLessEqual(len(out['description']), 60)

    def test_generate_camp_detail_story_mock_mode(self):
        async def run():
            return await dai.generate_camp_detail(
                club_id=1, target_kind='profile',
                base={'name': '流萤花谷', 'hardSpecs': {}},
                source_text='湖畔营地，有皮划艇与烧烤，24 小时热水。',
                media_paths=[], media=[
                    {'url': '/uploads/a.jpg', 'source': 'gallery',
                     'caption': '开阔湖景', 'tags': ['湖景'], 'usable': True}],
                template_id='story')
        out, usage = asyncio.run(run())
        self.assertEqual(out['templateId'], 'story')
        self.assertEqual(len(out['sections']), 7)
        self.assertEqual(out['sections'][0]['layout'], 'full')
        self.assertEqual(out['sections'][-1]['headline'], '出行须知')
        self.assertIsNotNone(usage)

    def test_default_template_unchanged(self):
        self.assertEqual(kit.get_template(None)['id'], 'classic')
        self.assertEqual(len(kit.get_template(None)['blueprint']), 4)


if __name__ == '__main__':
    unittest.main(verbosity=2)
