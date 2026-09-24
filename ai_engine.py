from __future__ import annotations
import json,re
from typing import Any
from ai_gateway import generate_json, record_mock_usage, GatewayResponse

SYSTEM = """你是 ClubOS 的 AI 活动内容主编（Editorial Director），不是模板填充器。
老板/领队只负责提供真实资料和照片，你负责像资深户外活动策划、编辑、文案与视觉主编一起工作一样，直接产出可发布成品。

最高规则：
1. 默认先做，不确认。能从资料判断的直接判断；只有来源内部出现会导致事实错误且无法消解的冲突，才写入 blocking_conflicts。
2. 允许创造表达，不允许创造事实。不能杜撰日期、地点、价格、领队、住宿、天气、景观必然出现、用户反馈、名额紧迫、资质等。
3. 区分 C 端公开事实、内部经营资料、宣传素材。成本单价、供应商报价、员工 SOP、内部退款谈判、毛利等不得进入 C 端。
4. 不选择固定模板，不返回 Family / Variant / Magazine / Diary 等模板名。你要针对当前活动自己决定：从哪里开场、什么最值得卖、图片与文字各占多少、信息何时出现、页面如何收束。
5. block 是视觉原子，不是固定章节。任何 block 都可省略、重复、自由排序；数量由内容决定。
6. 图片必须引用提供的 media ref，不得编造不存在的图片。根据横竖比、内容语义和叙事作用决定大图、双图、拼图或连续铺陈。
7. AI Credits 是计费层，不得因此削弱输入、减少图片、缩短上下文、换弱模型或减少创作深度。
8. 输出严格 JSON，不要 Markdown，不要解释。
"""

BLOCK_TYPES = "hero|lead|narrative|statement|media|gallery|facts|timeline|info|quote|divider|cta"


def _guess(text:str,patterns:list[str],default=''):
    for pat in patterns:
        m=re.search(pat,text,re.I)
        if m:return m.group(1).strip()
    return default


def _image_refs(source, n=None):
    refs=[x.get('ref') for x in source.get('images',[]) if x.get('ref')]
    return refs if n is None else refs[:n]


def _mock_activity(source:dict[str,Any]):
    """Offline demo only. It intentionally demonstrates editorial freedom, not template families.
    Live mode delegates structure decisions to the configured model.
    """
    text=source.get('text',''); imgs=_image_refs(source)
    is_ying='蓥华山' in text
    is_yoga='瑜伽' in text
    is_yuzixi='鱼子西' in text
    is_camp=any(k in text for k in ['露营','营地','湿地','皮划艇','桨板'])
    title=_guess(text,[r'(?:活动名称|活动主题|主题)[：:]?\s*([^\n]+)',r'(蓥华山[^\n]{0,20})',r'(鱼子西[^\n]{0,20})'],'周末自然计划')
    if is_ying and is_yoga:title='MOVE TO NATURAL｜蓥华山徒步 × 户外瑜伽'
    elif is_yuzixi:title='川西两天一夜｜新都桥 × 鱼子西'
    date=_guess(text,[r'(20\d{2}[./年-]\s*\d{1,2}[./月-]\s*\d{1,2}日?)',r'(\d{1,2}月\d{1,2}日)'],'待发布')
    location=_guess(text,[r'(?:活动地点|目的地|地点)[：:]?\s*([^\n]+)',r'(蓥华山|鱼子西|新都桥|青城山|四姑娘山)'],'成都周边')
    price=_guess(text,[r'(?:新客|活动费用|价格)[^\n]{0,20}?([0-9]{2,5})\s*元',r'([0-9]{2,5})\s*元\s*/?\s*(?:人|次)'],'0')
    distance=_guess(text,[r'徒步距离约?\s*([0-9.]+\s*公里)',r'([0-9.]+\s*KM)'],'')
    capacity=_guess(text,[r'(?:人数|规模)[^\n]{0,12}?([0-9]{1,3})\s*(?:人|组)'],'30')

    if is_ying and is_yoga:
        idea='先把身体打开，再走进森林。不是把瑜伽和徒步简单拼在一起，而是让一整天从呼吸、伸展自然过渡到山野行走。'
        understanding='品牌会员自然体验日：上午户外瑜伽建立身体状态，午后完成约6公里轻徒步；重点不是“打卡景区”，而是完整的一日自然节奏。'
    elif is_yuzixi:
        idea='把两天交给川西：雪山观景、藏地体验与高品质休息不抢戏，而是共同组成一段完整高原旅行。'
        understanding='目的地与体验并重的两日高品质户外旅行，应以景观期待与旅程节奏驱动，而不是罗列景点。'
    elif is_camp:
        idea='把周末过慢一点：水边、草地、露营和一顿认真吃的饭，让一天真正像在度假。'
        understanding='生活方式型户外体验，最值得卖的是场景和陪伴感，而不是复杂路线。'
    else:
        idea='把真实活动资料里最值得出发的理由放到前面，让用户先想去，再自然完成报名决策。'
        understanding='一场需要从现有素材中提炼核心动机并形成完整招募叙事的户外活动。'

    blocks=[]
    hero_media=imgs[:1]
    blocks.append({'type':'hero','headline':title,'kicker':location,'subtitle':idea,'mediaRefs':hero_media})
    blocks.append({'type':'lead','text':idea})
    if distance or date or location:
        vals=[]
        if date: vals.append({'label':'DATE','value':date})
        if distance: vals.append({'label':'HIKE','value':distance})
        if location: vals.append({'label':'PLACE','value':location})
        blocks.append({'type':'facts','items':vals[:4]})

    remaining=imgs[1:]
    if is_ying and is_yoga:
        if remaining[:3]: blocks.append({'type':'gallery','mediaRefs':remaining[:3],'caption':'先进入自然，再进入运动。'})
        blocks.append({'type':'narrative','eyebrow':'01 / OPEN THE BODY','headline':'上午，我们先不急着走','body':'抵达之后，先让身体从城市节奏里松开。呼吸、伸展、草地和山风共同构成这一天的第一部分。'})
        if remaining[3:5]: blocks.append({'type':'media','mediaRefs':remaining[3:5],'layout':'pair'})
        blocks.append({'type':'statement','text':'先用一小时把身体打开，再用六公里把自己交给森林。'})
        blocks.append({'type':'narrative','eyebrow':'02 / WALK INTO THE FOREST','headline':'午后，真正走进山里','body':'午饭以后进入蓥华山步道。页面不需要把景点写成百科，而是用沿途真实照片、路线信息和身体感受，让用户知道这段路为什么值得走。'})
    elif is_yuzixi:
        if remaining[:4]: blocks.append({'type':'gallery','mediaRefs':remaining[:4],'caption':'不是景点清单，是一段连续的高原旅程。'})
        blocks.append({'type':'statement','text':'真正让人记住川西的，往往不是一个地名，而是一路不断变化的光、雪山、草原和停下来的片刻。'})
        blocks.append({'type':'narrative','headline':'把目的地的期待感放在前面','body':'先用最有辨识度的景观建立出发欲望，再逐步交代住宿、文化体验与两日节奏。信息仍然完整，但不会压过旅行本身。'})
        if remaining[4:8]: blocks.append({'type':'gallery','mediaRefs':remaining[4:8]})
    elif is_camp:
        if remaining[:4]: blocks.append({'type':'gallery','mediaRefs':remaining[:4]})
        blocks.append({'type':'narrative','headline':'不是赶行程，是把一天过得舒服','body':'玩水、草地、吃饭、发呆都可以成为内容主角。AI应根据照片自己判断哪些瞬间值得放大，哪些只需要轻轻带过。'})
    else:
        if remaining[:4]: blocks.append({'type':'gallery','mediaRefs':remaining[:4]})
        blocks.append({'type':'narrative','headline':'为什么值得出发','body':'根据真实资料提炼最重要的体验价值，不用统一套“亮点一二三”。'})

    # Stable information may appear when it helps the decision, but is not the creative template.
    if '08:00' in text or '8:00' in text:
        blocks.append({'type':'timeline','title':'这一天怎么走','items':[
            {'time':'08:00','text':'集合 / 签到'},{'time':'10:00','text':'进入当天核心体验'},{'time':'12:00','text':'午餐 / 休整'},{'time':'17:00','text':'结束行程 / 返程'}]})
    if remaining[8:]: blocks.append({'type':'media','mediaRefs':remaining[8:12],'layout':'mosaic'})
    blocks.append({'type':'cta','headline':'想去，就把这一天留出来','text':'确认日期、费用与详细行程后即可报名。'})

    master={
        'title':title,'date':date,'location':location,
        'price':float(price) if str(price).isdigit() else 0,
        'capacity':int(capacity) if str(capacity).isdigit() else 0,
        'publicFacts':{'date':date,'location':location,'price':price,'distance':distance},
        'itinerary':[],'fees':{},'checklist':[],'services':[],
        'internalData':{},'uncertainties':[],'blocking_conflicts':[],
        'media':source.get('media_manifest',[]),
        'sourceSummary':text[:8000]
    }
    detail={
        'activityUnderstanding':understanding,
        'coreSellingIdea':idea,
        'editorialIntent':{'opening':'由当前活动最强动机决定','visualWeight':'由素材质量决定','template':'NONE'},
        'blocks':blocks
    }
    return {'activity_master':master,'detail':detail}


async def generate_activity(club_id:int,source:dict[str,Any])->tuple[dict[str,Any],GatewayResponse]:
    media=json.dumps(source.get('media_manifest',[]),ensure_ascii=False)
    prompt=f"""你收到的是老板/领队提供的完整活动原始资料。直接完成两件事：
A. 提取 Activity Master（事实与业务真相）；
B. 像内容主编一样生成 C 端招募详情的动态 block 方案。

原始资料（保留原始上下文）：
{source.get('text','')}

媒体清单（只能引用这些 ref）：
{media}

返回 JSON：
{{
  "activity_master":{{
    "title":"","date":"","location":"","price":0,"capacity":0,
    "publicFacts":{{}},"itinerary":[],"fees":{{}},"checklist":[],"services":[],
    "internalData":{{}},"uncertainties":[],"blocking_conflicts":[],"media":[]
  }},
  "detail":{{
    "activityUnderstanding":"",
    "coreSellingIdea":"",
    "editorialIntent":{{"opening":"","visualWeight":"","reason":""}},
    "blocks":[
      {{"type":"{BLOCK_TYPES}","headline":"可选","body":"可选","text":"可选","mediaRefs":["img_01"]}}
    ]
  }}
}}

block 语义：
hero=首屏；lead=短引言；narrative=图文叙事；statement=强观点短句；media=单图/双图/拼图；gallery=图片组；facts=关键事实条；timeline=时间线；info=必要决策信息；quote=引用；divider=节奏；cta=报名收束。
任何 block 都可省略、重复、自由排序。不要为了“结构完整”机械凑章节。照片多时主动做视觉编排，照片少时不要硬凑图片。
如果没有真正事实冲突，blocking_conflicts 必须为空，直接完成成品。"""
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,images=source.get('images'))
    if gw:return gw.data,gw
    data=_mock_activity(source); return data,record_mock_usage(club_id,'detail',prompt,data)


async def generate_channel(club_id:int,activity_master:dict[str,Any],detail:dict[str,Any],channel:str)->tuple[dict[str,Any],GatewayResponse]:
    labels={'wechat':'微信公众号','xhs':'小红书','poster':'活动招募海报','recap':'活动回顾'}
    prompt=f"""基于同一场活动，重新创作 {labels.get(channel,channel)} 原生内容。不是活动详情删减版。
Activity Master（事实）：{json.dumps(activity_master,ensure_ascii=False)}
招募详情的活动理解（可参考但不要照抄结构）：{json.dumps(detail,ensure_ascii=False)}

wechat: 根据公众号阅读场景重新决定标题、开篇、图文节奏与报名收束；返回 title, summary, blocks[]。
xhs: 返回 titleOptions[], hook, body, tags[], imageSequence[]；语言更像真实平台内容，不写公文。
poster: 返回 headline, subheadline, facts[], sellingPoints[], cta, preferredMediaRefs[]。
recap: 只有提供真实 actualActivityData / 现场素材时才能叙述实际发生事件；资料不足时明确返回 needsActualData=true，不编造。
严格 JSON。"""
    gw=await generate_json(club_id=club_id,task_type=channel,system_prompt=SYSTEM,user_prompt=prompt)
    if gw:return gw.data,gw
    title=activity_master.get('title','活动');idea=detail.get('coreSellingIdea','')
    if channel=='wechat':data={'title':title,'summary':idea,'blocks':detail.get('blocks',[])[:6]+[{'type':'cta','headline':'查看活动详情并报名'}]}
    elif channel=='xhs':data={'titleOptions':[title,f"周末去{activity_master.get('location','山里')}，这次不赶行程"],'hook':idea,'body':idea+'\n\n具体日期、费用和报名信息见活动详情。','tags':['户外','周末去哪儿','自然'],'imageSequence':[m.get('ref') for m in activity_master.get('media',[])[:9]]}
    elif channel=='poster':data={'headline':title,'subheadline':idea,'facts':[activity_master.get('date',''),activity_master.get('location','')],'sellingPoints':[idea],'cta':'扫码查看详情与报名','preferredMediaRefs':[m.get('ref') for m in activity_master.get('media',[])[:2]]}
    else:data={'needsActualData':True,'title':f'{title}｜活动回顾','message':'请上传现场照片或领队记录后再生成真实活动回顾。'}
    return data,record_mock_usage(club_id,channel,prompt,data)
