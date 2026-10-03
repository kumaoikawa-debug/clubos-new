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

BLOCK_TYPES = "hero|lead|narrative|statement|media|gallery|facts|timeline|info|quote|divider"


def _guess(text:str,patterns:list[str],default=''):
    for pat in patterns:
        m=re.search(pat,text,re.I)
        if m:return m.group(1).strip()
    return default


def _image_refs(source, n=None):
    refs=[x.get('ref') for x in source.get('images',[]) if x.get('ref')]
    return refs if n is None else refs[:n]


# 只有真实照片能进 C 端。品牌 logo / 字标 / 空白幻灯片底图 / 地图天气截图 / 扁平赞助商海报
# 会跟着 PPT 一起被抽出来，解析期已由 document_parser._image_kind 标成 kind='logo'。
# 这里按 ref 过滤。kind 缺失（老资料/PDF 渲染页）按照片处理，保持向后兼容。
def _photo_refs(source:dict[str,Any])->list[str]:
    imgs=[x for x in (source.get('images') or []) if isinstance(x,dict) and x.get('ref')]
    return [x['ref'] for x in imgs if (x.get('kind') or 'photo')=='photo']


# 「出行清单」由平台侧 renderPacking(master) 按 master.checklist 渲染（static/shared.js），
# 那一块才带商城匹配、平替与会员价。AI 若再出一块「装备建议」，同一份清单就会在页面上
# 出现两遍（2026-10-03 用户反馈「两个地方都有装备推荐」）。所以生成侧一律不出这类块，
# 标题命中下列词的 info 块由 _sanitize_blocks 在出口剔除——mock 与 live 两条路都收口。
_GEAR_BLOCK_TITLES=('装备建议','装备清单','出行清单','携带清单','携带物品','着装建议','行李清单',
                    '物品清单','建议装备','推荐装备','装备推荐','必备清单','自备物品','建议携带')


# ---------------------------------------------------------------------------
# 文本清洗：结构标记 / 行内噪声
#
# 上传的方案里，[Slide N] / [Page N] / [文件: xxx] 只是解析骨架，用来切分段落；
# 它们绝不能出现在 C 端成品里。以前没有任何一处清洗，于是行程的最后一格把
# 「[Slide 9] 餐食安排 …[Slide 12] 活动形式&参与人员安排…」整段倒进了页面。
# ---------------------------------------------------------------------------
_MARKER_RE=re.compile(r'\[(?:Slide|Page)\s*\d+\]|\[文件[:：][^\]]*\]')

# 方案里的小节标题（不是地点）。用于否决「把标题当地点」这类错误抽取。
_HEADING_BLOCKLIST={
    '目的地介绍','活动内容','着装建议','场地介绍','天气&交通','行程安排','行程安排&活动人员构成',
    '餐食安排','活动费用','执行保障','活动形式','活动形式&参与人员安排','参与人员安排',
    '介绍&活动亮点','活动亮点','活动安排','费用说明','注意事项','活动须知','活动说明','活动概述',
    '活动介绍','Q&A','参与达人','参与达人list','THANKS','目录',
}
# 只属于内部经营/执行侧的标记。命中这些词的行不进入 C 端成品。
_INTERNAL_MARKERS=('话术','禁区','门店领取','小票','收银','导购','店员','成本','毛利','返点','提成',
                   '结算','供应商','据实','不对外','内部','询价','档期','排期确认','同步至','经手')
# 「写给编辑自己看」的元话语。block 是给读者看的成品，这些字符串一旦上屏就是事故。
_META_PHRASES=('AI应','AI根据','应根据照片','你的任务','占位','示例','待补','TODO','模板','block(')

_KNOWN_PLACES=['山王坪','鱼子西','新都桥','蓥华山','毕棚沟','四姑娘山','达古冰川','青城山','牛背山',
               '海螺沟','木格措','塔公','丹巴','党岭','莫斯卡','格聂','冷噶错','子梅','稻城','亚丁',
               '色达','贡嘎','雨崩','哈巴','长坪沟','双桥沟','折多山','雅拉','红岩顶','达瓦更扎']


def _strip_markers(s:str)->str:
    return _MARKER_RE.sub('',s or '')


def _clean(s:str,limit:int=0)->str:
    """单行清洗：去结构标记、压空白、去首尾标点。用于「解析后的一格短文本」。"""
    s=_strip_markers(s or '')
    s=re.sub(r'\s+',' ',s).strip()
    s=s.strip('|·、，,；;。 ')
    if limit and len(s)>limit:
        s=s[:limit].rstrip('，,、；;。 ')+'…'
    return s


def _clean_text(s:str)->str:
    """多行清洗：保留换行（narrative.body 用 \\n 分句），只去结构标记与多余空行。"""
    s=_strip_markers(s or '')
    s=re.sub(r'[ \t]+',' ',s)
    s=re.sub(r'\n\s*\n+','\n',s)
    return s.strip()


def _is_internal(s:str)->bool:
    return any(k in (s or '') for k in _INTERNAL_MARKERS)


def _looks_like_heading(s:str)->bool:
    """判断一个候选串是不是「小节标题」而不是内容（用于否决错误的地点抽取）。"""
    t=_clean(s)
    if not t:return True
    if t.replace(' ','') in {h.replace(' ','') for h in _HEADING_BLOCKLIST}:return True
    # 很短、且不含数字与地理词 → 更像标题而不是地点
    return len(t)<=8 and not re.search(r'[0-9]|省|市|区|县|镇|山|湖|沟|村|岛|园|寺|峰|湾|海|川|营',t)


def _list_items(s:str,limit:int=12)->list[str]:
    """把「A、B、C；D」这类清单串拆成条目，去掉序号、括注与「等用品」这类尾巴。"""
    out=[]
    for part in re.split(r'[、,，/;；\s]+',_clean(s)):
        p=re.sub(r'[（(][^）)]*[）)]','',part)
        p=re.sub(r'^(?:穿着|可备|带上|携带|自备|备好|需备|备)\s*','',p)
        p=re.sub(r'^(?:等用品|等等|等)\s*$','',p)
        p=re.sub(r'(?:等用品|等等)$','',p)
        p=re.sub(r'^[0-9]+[.、)）]?','',p).strip(' ·。.')
        if 2<=len(p)<=14 and p not in out:out.append(p)
        if len(out)>=limit:break
    return out


# 服务条目的「主题」归类。同一个主题只留一条：画像里写好的版本在前、优先保留，
# 抽取版里语义重复的那条丢弃，避免出现「保障：PICC 户外活动意外险」与
# 「保障：户外活动意外险（PICC）」两条几乎同义的话并排显示。
_SERVICE_TOPICS=(('保险','意外险','PICC'),('瑜伽',),('户外团队','持证','向导','导游'),('住宿','酒店'),
                 ('唐卡',),('藏装','藏服'),('糌粑','酥油茶'),('大巴','交通','车费'),('摄影','跟拍'))


def _merge_services(items)->list[str]:
    out=[]; used=set()
    for it in items:
        it=_clean(it)
        if not it or _is_internal(it): continue
        topic=None
        for i,keys in enumerate(_SERVICE_TOPICS):
            if any(k in it for k in keys): topic=i; break
        if topic is not None:
            if topic in used: continue
            used.add(topic)
        elif it in out: continue
        out.append(it)
    return out


def _merge_checklist(items)->list[str]:
    """装备清单去重：'徒步鞋' 与 '防水防滑的徒步鞋' 是同一件东西，只留先出现的那个。"""
    out=[]
    for raw in items:
        c=_clean(raw)
        if not (2<=len(c)<=14): continue
        if any(c in o or o in c for o in out): continue
        out.append(c)
    return out


def _parse_itinerary(raw:str)->list[dict[str,str]]:
    """解析行程。

    优先按「时间 | 项目 | 内容」表格行解析（PPT/Word 里行程最常见的写法）：
    表格天然以行分界，不会越界吞掉后面的章节。旧实现只用「HH:MM-HH:MM」正则扫全文，
    表格最后一行会一路吃到文档结尾，把 [Slide 9]~[Slide 12] 的原文整段塞进行程——
    这是「活动详情乱、没逻辑」最直观的一处。
    """
    items=[]
    rng=re.compile(r'^\d{1,2}:\d{2}\s*[-–—~～至到]\s*\d{1,2}:\d{2}$')
    one=re.compile(r'^\d{1,2}:\d{2}$')
    for line in raw.split('\n'):
        line=line.strip()
        if not line or line.startswith('[') or _is_internal(line):continue
        if '|' not in line:continue
        cells=[c for c in (_clean(x) for x in line.split('|')) if c]
        if len(cells)<2 or not (rng.match(cells[0]) or one.match(cells[0])):continue
        body='：'.join([cells[1]]+([' '.join(cells[2:])] if len(cells)>2 else []))
        body=_clean(body,80)
        if body:items.append({'time':cells[0],'content':body})
    if items:return items
    # 兜底：没有表格时按「时间段 + 同行描述」扫，描述止于行尾，绝不跨行。
    seg=re.compile(r'(\d{1,2}):(\d{2})\s*[-–—~–至到]\s*(\d{1,2}):(\d{2})\s*([^\n]*)')
    for m in seg.finditer(raw):
        desc=_clean(re.split(r'\s{2,}|\[',_clean(m.group(5)))[0],80)
        if not desc:continue
        items.append({'time':f'{int(m.group(1))}:{m.group(2)}–{int(m.group(3))}:{m.group(4)}','content':desc})
    return items


_LIST_SECTION_MARKERS=('装备建议','着装建议','装备清单','携带清单','必备清单','自备物品','出行清单','需携带','需准备')
_HIGHLIGHT_SKIP=('穿着','携带','自备','建议：','费用包含','包含：')


def _extract_highlights(raw:str,limit:int=5)->list[dict[str,str]]:
    """抽取介绍页里的编号条目（1. 2. 3. …）——这是方案里最「能卖」的原生内容。

    只取「目的地介绍 / 活动亮点」这类描述性条目：一旦进入装备·清单类小节就停止，
    否则会把「1. 穿着防水防滑的徒步鞋、…」这种清单项当成景点看点。
    """
    out=[]
    for line in raw.split('\n'):
        s=_clean(line)
        if not s or _is_internal(s):continue
        if any(k in s for k in _LIST_SECTION_MARKERS):break
        m=re.match(r'^[0-9]{1,2}\s*[.、)）]\s*(.{2,70})',s)
        if not m:continue
        desc=_clean(m.group(1),60)
        if any(k in desc for k in _HIGHLIGHT_SKIP) or desc.count('、')>=2:continue
        title=_clean(re.split(r'[（(]',desc)[0],22)
        if len(title)<2:continue
        out.append({'title':title,'desc':desc})
        if len(out)>=limit:break
    return out


def _collect_list_block(raw:str,labels:tuple[str,...],limit:int=14)->list[str]:
    """从「XX装备建议：」这类小节里，按行收集到下一个标题为止，再拆成清单条目。"""
    lines=raw.split('\n')
    for i,line in enumerate(lines):
        if any(l in line for l in labels):
            buf=[]
            for nxt in lines[i+1:i+9]:
                s=nxt.strip()
                if not s:break
                if len(s)<=12 and not re.match(r'^[0-9]',s) and not re.search(r'[、，。;；]',s):break
                buf.append(s)
            if buf:return _list_items(' '.join(buf),limit)
    return []


def _extract_location(raw:str)->str:
    """地点抽取。核心是「绝不把小节标题当地点」——这是旧版把 location 写成
    「介绍&活动亮点」的直接原因（它只是 Slide 2 的标题）。"""
    for pat in (r'(?:活动地点|目的地|地点|地址)[：:]\s*([^\n|]{2,30})',):
        m=re.search(pat,raw)
        if m:
            v=_clean(m.group(1))
            if v and not _looks_like_heading(v):return v
    m=re.search(r'([\u4e00-\u9fa5]{2,8}?(?:省|市|州|区|县))[\u4e00-\u9fa5]{0,8}?'
                r'([\u4e00-\u9fa5]{2,8}?(?:镇|乡|景区|公园|山|湖|沟|村|岛))',raw)
    if m:
        v=_clean(m.group(0))
        if v and not _looks_like_heading(v):return v
    for p in _KNOWN_PLACES:
        if p in raw:return p
    m=re.search(r'([\u4e00-\u9fa5]{2,6}(?:营地|古镇|景区|公园|山|湖|沟|湾|岛))',raw)
    if m:
        v=_clean(m.group(1))
        if v and not _looks_like_heading(v):return v
    return ''


def _extract_structured(source:dict[str,Any])->dict[str,Any]:
    """从原始资料文本里「抽取」结构化事实：行程、费用、服务、清单、人数、价格、地点、亮点。

    严格只做抽取、不做创造：抽不到就留空，由 _mock_activity 回落到既有启发式。
    这样即使没有接通大模型（演示引擎/离线），上传的 PPT/Word 方案里的行程与费用明细
    也会真实出现在活动详情里，而不是被固定模板忽略——这正是「上传了方案却完全没按方案」的根因之一。
    """
    raw=_strip_markers((source or {}).get('text','') or '')
    out={'title':'','date':'','location':'','price':0,'capacity':0,'distance':'',
         'itinerary':[],'days':[],'fees':{},'services':[],'checklist':[],'highlights':[]}
    if not raw.strip(): return out

    out['itinerary']=_parse_itinerary(raw)
    out['highlights']=_extract_highlights(raw)
    day_marks=[(m.start(),int(re.search(r'\d+',m.group(0)).group(0)))
               for m in re.finditer(r'(?:DAY|Day|D|第)\s*(\d+)\s*(?:天|日)?',raw)]
    out['days']=sorted({n for _,n in day_marks if n>0})

    # ---- 距离 / 人数 / 人均价格 / 合计 / 地点 / 日期 / 标题 ----
    d=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(?:km|KM|Km|公里)\s*(?:轻徒步|徒步|环线|线路|往返)?',raw)
    if d: out['distance']=_clean(d.group(1))+'km'

    cap=re.search(r'(?:活动人数|成行人数|人数|规模)\s*[：:]?\s*(\d{1,3})\s*人',raw) \
        or re.search(r'(\d{1,3})\s*人\s*(?:整车成行|成行|规模)',raw)
    if not cap:
        # 口语化写法兜底：「带 12 个会员去…」「限 20 人」——只认 2~3 位、且后缀明确是人，
        # 避免把「6 公里」「人均 288 元」误当人数，也排除「10 人一桌 / 4 人一车」这类计量单位。
        for m in re.finditer(r'(\d{2,3})\s*(?:个|位|名)?\s*(?:会员|人)(?!均|民|数|一?\s*(?:桌|围|锅|份|车|排|房))',raw):
            n=int(m.group(1))
            if 2<=n<=500: cap=m; break
    if cap: out['capacity']=int(cap.group(1))
    per=(re.search(r'人均费用\s*[¥￥]?\s*([0-9][0-9,]*(?:\.\d+)?)',raw)
         or re.search(r'人均[^\n]{0,6}?[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)',raw)
         # 无货币符号的口语写法：「人均288元」「每人 288」「人均价格 288」
         or re.search(r'(?:人均(?:费用|价格)?|每人)\s*[¥￥]?\s*([0-9][0-9,]*(?:\.\d+)?)\s*元?',raw)
         # 套餐式报价：「498/次」「498 元/次」
         or re.search(r'([0-9][0-9,]{1,5})\s*元?\s*/\s*(?:次|人|位|场)',raw))
    total=re.search(r'未含税\s*[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)',raw) or re.search(r'合计[^\n]{0,20}?[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)',raw)
    if per:
        try: out['price']=float(per.group(1).replace(',',''))
        except ValueError: pass
    out['location']=_extract_location(raw)
    dts=re.findall(r'(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?',raw)
    if dts: out['date']=f'{dts[0][0]}-{int(dts[0][1]):02d}-{int(dts[0][2]):02d}'
    # 标题：优先「X天X夜」行程名；再退回资料里写明的活动名称。
    # 不再用「目的地名兜底」——那会把行政地名当标题，交给 _mock_activity 组装更干净。
    tit=re.search(r'([\u4e00-\u9fa5]{2,12}?[两二三四]天[一二三四]夜)',raw)
    if not tit: tit=re.search(r'(?:活动名称|活动主题|主题)[：:]?\s*([^\n]{2,24})',raw)
    if tit: out['title']=_clean(tit.group(1)).strip('·')

    # ---- 费用说明：只写资料里出现过的条目 ----
    items=[]
    for kw in ['车费','保姆大巴','越野中转车','中转车','午餐','晚餐','特色餐','住宿','门票','唐卡体验','唐卡',
               '饮用水','氧气','摄影','领队','保险','工作餐','策划执行']:
        if kw in raw and kw not in items: items.append(kw)
    if per: out['fees']['人均费用']='¥'+per.group(1)
    if total: out['fees']['合计（未含税）']='¥'+total.group(1)
    if cap: out['fees']['按人数报价']=cap.group(1)+' 人'
    if items: out['fees']['费用包含']='、'.join(items[:14])
    if ('未含税' in raw) or ('成本预估' in raw): out['fees']['备注']='本表为方案成本预估，未含税金；价格随季节浮动。'

    # ---- 服务：只写资料里明确提到的，不凭空补充；命中内部标记的一律丢弃 ----
    services=[]
    def _add(s):
        s=_clean(s)
        if s and not _is_internal(s) and s not in services: services.append(s)
    if ('保姆大巴' in raw) or ('旅游营运' in raw): _add('交通：正规旅游保姆大巴（持证旅游营运资质）')
    if '赞巴明镜酒店' in raw: _add('住宿：新都桥·赞巴明镜酒店（两晚连住不挪行李）')
    elif '住宿' in raw: _add('住宿：'+((out['location'] or '目的地'))+'当地安排')
    if '唐卡' in raw: _add('体验：国家非遗唐卡绘制')
    if ('藏装' in raw) or ('藏服' in raw): _add('体验：康巴藏装换装打卡')
    if ('糌粑' in raw) or ('酥油茶' in raw): _add('体验：酥油茶 / 手工糌粑品鉴')
    if ('PICC' in raw) or ('户外保险' in raw) or ('意外险' in raw): _add('保障：户外活动意外险（PICC）')
    elif '保险' in raw: _add('保障：含户外旅游保险')
    if '瑜伽' in raw: _add('体验：户外瑜伽（垫子与活动道具由主办方提供）')
    if ('持证' in raw) and (('户外' in raw) or ('导游' in raw) or ('向导' in raw)): _add('保障：专业持证户外工作人员 / 导游全程随行')
    if ('专业户外' in raw) or ('户外团队' in raw): _add('保障：专业户外团队全程随行')
    if '摄影' in raw: _add('服务：全程摄影师跟拍')
    out['services']=services

    # ---- 出行清单：优先「装备建议 / 着装建议」小节，其次通用清单标题 ----
    out['checklist']=_collect_list_block(raw,('装备建议','着装建议','装备清单','携带物品','携带清单','必备清单','自备物品','出行清单','需携带','需准备'))
    return out


# ---------------------------------------------------------------------------
# 目的地画像：离线演示引擎的「编辑判断」。
#
# 接通真实大模型时不会用到；它存在的意义是——在没有模型的环境里（本地演示、
# 模型不可用时的兜底），也必须按真实资料产出有叙事、有吸引力的成品，
# 而不是把 PPT 原文倒进页面。所有内容均取自用户上传方案的公开事实。
# ---------------------------------------------------------------------------
_DESTINATION_PROFILES=[
    {
        'keys':['山王坪'],
        'title':'山王坪赏秋｜森林瑜伽 × 5km 林海徒步',
        'location':'重庆 · 南川 · 山王坪',
        'distance':'5km 轻徒步',
        'idea':'从北城天街出发约两小时，走进中国首批森林氧吧：上午在多片树林之间做一场森林瑜伽，下午沿 5 公里步道穿过「平分秋色」的双色杉林。',
        'understanding':'城市近郊的一日自然体验。真正的卖点是「两小时车程就能抵达的秋天」，而不是徒步强度——所以开场先给季节与森林，再交代瑜伽与路线，最后才收束到费用与保障。',
        'beats':[
            {'eyebrow':'两小时之外','headline':'车程两小时，气温先降下来',
             'body':'山王坪海拔约 1300 米，森林覆盖率 96%，是中国首批森林氧吧。\n十月底，这里的杉林刚开始变色。',
             'pull':'把周末交给森林'},
            {'eyebrow':'平分秋色','headline':'一条公路，两边是不同的季节',
             'body':'一侧水杉正在转金黄，另一侧柳杉四季常绿。\n登上林海观景塔，两种颜色在脚下泾渭分明。',
             'pull':'一边是秋，一边是春'},
            {'eyebrow':'林间瑜伽','headline':'先让身体安静，再开始走路',
             'body':'瑜伽场地藏在多片树林之间，可按当天光线任选一处。\n垫子与道具都由主办方备好，你只需要带上自己。',
             'pull':'呼吸先于脚步'},
        ],
        'statements':['两小时车程，走进一整片秋天。'],
        'checklist':['徒步鞋','速干衣裤','防晒外套','单日徒步小背包','防晒帽','登山杖','充电宝','雨衣','护唇膏'],
        'services':['体验：森林瑜伽（瑜伽垫与活动道具由主办方提供）','保障：PICC 户外活动意外险','保障：专业户外团队全程随行'],
    },
    {
        'keys':['蓥华山'],
        'title':'MOVE TO NATURAL｜蓥华山徒步 × 户外瑜伽',
        'location':'四川 · 什邡 · 蓥华山',
        'distance':'6km 轻徒步',
        'idea':'先把身体打开，再走进森林。不是把瑜伽和徒步简单拼在一起，而是让一整天从呼吸、伸展自然过渡到山野行走。',
        'understanding':'品牌会员自然体验日：上午户外瑜伽建立身体状态，午后完成约六公里轻徒步；重点不是「打卡景区」，而是完整的一日自然节奏。',
        'beats':[
            {'eyebrow':'上午｜打开身体','headline':'先不急着走',
             'body':'抵达之后，先让身体从城市节奏里松开。\n呼吸、伸展、草地与山风，构成这一天的第一部分。',
             'pull':'呼吸先于脚步'},
            {'eyebrow':'午后｜走进森林','headline':'再把自己交给山野',
             'body':'午饭以后进入蓥华山步道，完成约六公里轻徒步。\n路线不长，刚好够一整天完整地落在身体上。',
             'pull':'六公里，刚好'},
        ],
        'statements':['先用一小时把身体打开，再用六公里把自己交给森林。'],
        'checklist':['徒步鞋','速干衣裤','防晒外套','瑜伽服','单日徒步小背包','防晒帽','登山杖','充电宝'],
        'services':['体验：户外瑜伽（垫子与活动道具由主办方提供）','保障：含户外旅游保险','保障：专业户外团队全程随行'],
    },
    {
        'keys':['鱼子西','新都桥'],
        'title':'川西两天一夜｜新都桥 × 鱼子西',
        'location':'四川 · 康定 · 新都桥 · 鱼子西',
        'distance':'',
        'idea':'把两天交给川西：雪山观景、藏地体验与高品质休息不抢戏，而是共同组成一段完整的高原旅行。',
        'understanding':'目的地与体验并重的两日高品质户外旅行，应以景观期待与旅程节奏驱动，而不是罗列景点。',
        'beats':[
            {'eyebrow':'两天一夜','headline':'高原把节奏放慢了',
             'body':'两天时间不赶路，把景观、体验与休息都放进去。\n第一天的路程本身就是风景。',
             'pull':'慢慢走，看得更多'},
            {'eyebrow':'光落下来','headline':'川西最动人的从来不是地名',
             'body':'一路不断变化的光、雪山、草原，和停下来的那些片刻，才是这趟行程真正被记住的部分。',
             'pull':'记住的是光'},
        ],
        'statements':['真正让人记住川西的，不是一个地名，而是一路不断变化的光。'],
        'checklist':['冲锋衣','抓绒外套','徒步鞋','保暖帽','手套','墨镜','防晒霜','保温杯','登山杖','充电宝'],
        'services':['住宿：新都桥当地安排','保障：含户外旅游保险','保障：专业户外团队全程随行'],
    },
]


def _profile_for(text:str)->dict[str,Any]|None:
    for p in _DESTINATION_PROFILES:
        if any(k in text for k in p['keys']): return p
    return None


def _forms(text:str)->list[str]:
    return [k for k in ('徒步','登山','瑜伽','露营','骑行','桨板','皮划艇','漂流','滑雪','探洞',
                        '温泉','观星','摄影','越野','溯溪') if k in text]


def _generic_idea(text:str,location:str,st:dict[str,Any])->str:
    place=location or '目的地'
    forms=_forms(text); hl=st.get('highlights') or []
    if hl and forms:
        return f'{place}，{hl[0]["title"]}。一天里安排{"、".join(forms[:2])}，不赶行程。'
    if hl: return f'{place}，{hl[0]["title"]}。'
    if forms: return f'{place}的一日{forms[0]}，把周末交给山野。'
    return f'{place}的户外一日，从集合出发到返程都已经安排好。'


def _generic_understanding(text:str,location:str,st:dict[str,Any],hl:list[dict[str,str]])->str:
    place=location or '目的地'
    kind='、'.join(_forms(text)[:2]) or '户外体验'
    if hl: return f'以{place}的{hl[0]["title"]}为核心卖点，由{kind}串起一天的节奏。'
    return f'一场以{kind}为主线的户外一日行程，卖点是现场体验，而不是路线数据。'


def _generic_beats(hl:list[dict[str,str]],text:str,location:str)->list[dict[str,str]]:
    place=location or '这里'
    beats=[]
    if hl:
        beats.append({'eyebrow':'为什么是这里','headline':_clean(hl[0]['title'],16),'body':hl[0]['desc'],'pull':''})
    if len(hl)>1:
        beats.append({'eyebrow':'现场看点','headline':'值得为它出发',
                      'body':'\n'.join(h['title'] for h in hl[1:4]),'pull':''})
    forms=_forms(text)
    if forms:
        beats.append({'eyebrow':'怎么玩','headline':'一天这样安排',
                      'body':f'以{forms[0]}为主线，其余时间留给休息、吃饭和看风景。','pull':''})
    if not beats:
        beats.append({'eyebrow':'出发','headline':f'去{place}走一趟',
                      'body':f'{place}的这一天，从集合出发到返程都安排好了。','pull':''})
    return beats


def _sanitize_blocks(blocks:list[dict[str,Any]],allowed_refs:set[str]|None=None)->list[dict[str,Any]]:
    """最终防线：清掉残留的结构标记、「写给编辑自己看」的元话语，以及不该上屏的图片。

    block 是给 C 端读者看的成品。[Slide 9]、「AI应根据照片自己判断哪些瞬间值得放大」
    这类字符串一旦上屏，就是最刺眼的事故；品牌 logo / 字标 / 空白底图被当成活动照片，
    同样是事故。这里统一收口，任何出口都不许漏。

    allowed_refs：允许出现在成品里的图片 ref 集合（真实照片）。传 None 表示不按图片过滤。
    """
    out=[]
    for b in blocks or []:
        if not isinstance(b,dict): continue
        btype=str(b.get('type') or '')
        # 「出行清单」已由平台 renderPacking(master) 按 master.checklist 渲染（带商城匹配与会员价）。
        # 生成侧再出一块「装备建议」= 同一份清单在页面上出现两遍，这里直接剔除。
        if btype=='info':
            ttl=str(b.get('title') or b.get('headline') or '')
            if any(g in ttl for g in _GEAR_BLOCK_TITLES): continue
        nb={}
        for k,v in b.items():
            if isinstance(v,str): nb[k]=_clean_text(v)
            elif isinstance(v,list):
                nv=[]
                for it in v:
                    if isinstance(it,dict): nv.append({kk:(_clean_text(vv) if isinstance(vv,str) else vv) for kk,vv in it.items()})
                    elif isinstance(it,str): nv.append(_clean_text(it))
                    else: nv.append(it)
                nb[k]=nv
            else: nb[k]=v
        # 图片只留真实照片；纯图片块被清空后整块丢弃（文字块保留，只是没有配图）
        if allowed_refs is not None and isinstance(nb.get('mediaRefs'),list):
            nb['mediaRefs']=[r for r in nb['mediaRefs'] if r in allowed_refs]
            if btype in ('media','gallery') and not nb['mediaRefs']: continue
        texts=[str(nb.get(k) or '') for k in ('headline','subtitle','body','text')]
        if any(p in t for t in texts for p in _META_PHRASES): continue
        out.append(nb)
    return out


def _mock_activity(source:dict[str,Any]):
    """Offline demo only. It demonstrates editorial judgement, not template families.

    Live mode delegates structure decisions to the configured model. 未接入大模型时，
    这里必须靠「目的地画像 + 资料抽取的事实」产出有开场、有叙事、有事实、有保障的成品；
    任何情况下都不得把 [Slide N]、「AI应根据照片…」这类中间字符串泄露到 C 端。
    """
    text=_strip_markers(source.get('text','') or '')
    # 只用真实照片：品牌 logo / 字标 / 空白底图 / 截图在解析期已被标成 kind='logo'。
    # 不做「一张照片都没有就退回全部图片」的兜底——那等于把品牌 logo 又放回头图，
    # 正是用户明确要求不要出现的东西；没有照片时 hero 交给平台封面/底色兜底。
    imgs=_photo_refs(source)
    st=_extract_structured(source)
    prof=_profile_for(text) or {}
    hl=st.get('highlights') or []

    # ---- 事实：画像 > 资料抽取 > 兜底组装（绝不用小节标题当地点/标题）----
    location=prof.get('location') or st.get('location') or ''
    title=prof.get('title') or st.get('title') or ''
    if not title:
        forms=_forms(text)
        if location and forms: title=f'{location}｜{forms[0]}'
        elif location: title=location
        elif forms: title=f'周末{forms[0]}计划'
        else: title='周末自然计划'
    date=st.get('date') or _guess(text,[r'(20\d{2}[./年-]\s*\d{1,2}[./月-]\s*\d{1,2}日?)',r'(\d{1,2}月\d{1,2}日)'],'待发布')
    distance=prof.get('distance') or st.get('distance') or ''
    price_val=float(st['price']) if st.get('price') else 0.0
    # 人数只在资料里明确写了才对外展示：默认 30 只是给团期用的可编辑初值，不是活动事实，
    # 直接印进「服务与保障」等于替老板对外承诺了一个成行人数。
    cap_extracted=bool(st.get('capacity'))
    capacity=_guess(text,[r'(?:人数|规模)[^\n]{0,12}?([0-9]{1,3})\s*(?:人|组)'],'30')
    cap_val=int(st['capacity']) if cap_extracted else (int(capacity) if str(capacity).isdigit() else 30)
    price_text=('¥'+('%g'%st['price'])) if st.get('price') else ''

    idea=prof.get('idea') or _generic_idea(text,location,st)
    understanding=prof.get('understanding') or _generic_understanding(text,location,st,hl)

    blocks=[]
    blocks.append({'type':'hero','headline':title,'kicker':location or '户外活动','subtitle':idea,'mediaRefs':imgs[:1]})
    blocks.append({'type':'lead','text':idea})

    facts=[]
    if date and date!='待发布': facts.append({'label':'DATE','value':date})
    if distance: facts.append({'label':'HIKE','value':distance})
    if location: facts.append({'label':'PLACE','value':location})
    if price_text: facts.append({'label':'PRICE','value':price_text})
    if facts: blocks.append({'type':'facts','items':facts[:4]})

    remaining=imgs[1:]
    if remaining[:4]: blocks.append({'type':'gallery','mediaRefs':remaining[:4]})

    # ---- 叙事：画像给「编辑判断」，没有画像时用资料里抽取的真实亮点 ----
    beats=prof.get('beats') or _generic_beats(hl,text,location)
    def _narr(b):
        return {'type':'narrative','eyebrow':b.get('eyebrow',''),'headline':b.get('headline',''),
                'body':b.get('body',''),'pull':b.get('pull','')}
    if beats: blocks.append(_narr(beats[0]))
    stmts=prof.get('statements') or []
    if stmts: blocks.append({'type':'statement','text':stmts[0]})
    for i,b in enumerate(beats[1:3]):
        if i==0 and remaining[4:6]: blocks.append({'type':'media','mediaRefs':remaining[4:6],'layout':'pair'})
        blocks.append(_narr(b))

    # ---- 事实信息：行程 / 服务与保障 / 装备建议 ----
    if st.get('itinerary'):
        blocks.append({'type':'timeline','title':'行程安排',
                       'items':[{'time':it['time'],'text':it['content']} for it in st['itinerary'][:16]]})

    services=_merge_services(list(prof.get('services') or [])+list(st.get('services') or []))
    info=[]
    if st.get('fees',{}).get('人均费用'): info.append('人均费用 '+st['fees']['人均费用'])
    if cap_extracted: info.append('成行人数 '+str(cap_val)+' 人')
    info+=services
    if info: blocks.append({'type':'info','title':'服务与保障','items':info[:10]})

    # 出行清单已由平台侧 renderPacking(master) 按 master.checklist 渲染（带商城匹配 / 平替 /
    # 会员价），这里绝不另出一块「装备建议」——否则同一份清单在页面上出现两遍。
    # checklist 仍然写进 master，供平台那一块使用。
    checklist=_merge_checklist(list(prof.get('checklist') or [])+list(st.get('checklist') or []))

    if remaining[6:]: blocks.append({'type':'media','mediaRefs':remaining[6:10],'layout':'mosaic'})

    master={
        'title':title,'date':date,'location':location,
        'price':price_val,
        'capacity':cap_val,
        'publicFacts':{'date':date,'location':location,'price':price_text,'distance':distance},
        'itinerary':st.get('itinerary') or [],
        'fees':st.get('fees') or {},
        'checklist':checklist,
        'services':services,
        'internalData':{},'uncertainties':[],'blocking_conflicts':[],
        'media':media_catalog(source),
        'sourceSummary':text[:8000]
    }
    detail={
        'activityUnderstanding':understanding,
        'coreSellingIdea':idea,
        'editorialIntent':{'opening':'由当前活动最强的出发动机决定','visualWeight':'由素材质量决定','template':'NONE'},
        'blocks':_sanitize_blocks(blocks)
    }
    return {'activity_master':master,'detail':detail}


def media_catalog(source:dict[str,Any],master:dict[str,Any]|None=None)->list[dict[str,Any]]:
    """权威媒体清单：ref → url 只能来自真实上传/磁盘资料，模型无权定义媒体条目。

    live 模式下模型会照着 prompt 里的清单回写 media，但它只会写 ["img_01",...] 这种纯 ref，
    于是前端 mediaMap 拿不到任何 url：所有图片退化成灰色占位块、头图退化成纯色块。
    这里以 source 的媒体清单为准重建，模型在 blocks 里只被允许「引用」ref。
    """
    # kind 由解析期判定（photo / logo）：它既让 live 提示词能要求「只用 photo」，
    # 也让前端在需要时能知道某张图是品牌标而不是照片。缺失按 photo 处理。
    keys=('ref','name','url','width','height','orientation','source','page','kind')
    catalog={}
    def add(x):
        if not isinstance(x,dict):return
        ref=str(x.get('ref') or '').strip()
        if not ref:return
        cur=catalog.get(ref)
        if cur is None:
            if not str(x.get('url') or '').strip():return   # 没有 url 的条目对渲染毫无价值，不能进清单
            catalog[ref]={k:x.get(k) for k in keys if x.get(k) is not None}
            return
        # 同一个 ref 会在 media_manifest / images / 模型输出里各出现一次，缺什么补什么，而不是丢掉整条。
        for k in keys:
            if cur.get(k) in (None,'') and x.get(k) not in (None,''):cur[k]=x[k]
    for x in (source or {}).get('media_manifest') or []:add(x)
    for x in (source or {}).get('images') or []:add(x)
    for x in (master or {}).get('media') or []:add(x)   # 老数据里已带 url 的条目照样保留
    return list(catalog.values())


def detail_outline(detail:dict[str,Any])->str:
    """「换一版」用的上一版结构摘要：只给 block 顺序与短标题，让模型知道别重复，又不至于照抄。"""
    parts=[]
    for b in (detail or {}).get('blocks') or []:
        if not isinstance(b,dict): continue
        kind=str(b.get('type') or '?')
        head=re.sub(r'\s+',' ',str(b.get('headline') or b.get('title') or b.get('text') or '')).strip()[:36]
        parts.append(f'{kind}({head})' if head else kind)
    return ' → '.join(parts)


def _revision_block(direction:str,previous_detail:dict[str,Any]|None,version_no:int)->str:
    """换版要求段落。第一版且没有额外要求时不出现，避免干扰「少问先做」。"""
    direction=(direction or '').strip()
    outline=detail_outline(previous_detail or {})
    if version_no<=1 and not direction: return ''
    lines=[f'\n这是第 {version_no} 版，不是第一版。老板对上一版不满意，成品必须明显不同：开场方式、图片与文字的权重、区块顺序都要换，标题与句子不得与上一版重复。']
    if outline: lines.append(f'上一版的页面结构（只用于避免重复，不要照抄）：{outline}')
    if direction: lines.append(f'老板的换版要求（必须满足，但依然不得创造事实）：{direction}')
    else: lines.append('老板没有指定方向：请自己换一个最能促成报名的切入角度重做。')
    return '\n'.join(lines)+'\n'


def _rotate_layout(blocks:list[dict[str,Any]],variant:int)->list[dict[str,Any]]:
    """演示模式（mock）专用：按版本号做真实的结构轮换。

    真实调用时版式由模型自己决定；这里只是让没接通大模型的环境也能看到「换一版确实换了」，
    而不是反复吐出同一份成品。轮换只调整区块顺序与图片权重，不编造任何事实。
    """
    if variant<=0 or not blocks: return blocks
    out=list(blocks)
    def pull(kind):
        for i,b in enumerate(out):
            if isinstance(b,dict) and b.get('type')==kind: return out.pop(i)
    if variant%3==1:
        for kind,pos in (('facts',1),('statement',2)):
            b=pull(kind)
            if b: out.insert(min(pos,len(out)),b)
    elif variant%3==2:
        for b in out:
            refs=b.get('mediaRefs') if isinstance(b,dict) else None
            if isinstance(b,dict) and b.get('type')=='gallery' and refs and len(refs)>2:
                b['mediaRefs']=refs[:2]
        b=pull('statement')
        if b: out.insert(1,b)
    else:
        b=pull('gallery')
        if b: out.insert(1,b)
    # 兜底：若上面的定向轮换因为该模板缺少对应 block 而没有任何变化，
    # 做一次确定性整体循环移位，保证 mock 模式下「换一版」肉眼可见地不同，
    # 且不编造任何事实、不改动事实区块的内容，只换顺序。
    if out == list(blocks) and len(out) > 1:
        k = variant % len(out)
        if k:
            out = out[k:] + out[:k]
    return out


async def generate_activity(club_id:int,source:dict[str,Any],*,direction:str='',
                            previous_detail:dict[str,Any]|None=None,
                            version_no:int=1)->tuple[dict[str,Any],GatewayResponse]:
    media=json.dumps(source.get('media_manifest',[]),ensure_ascii=False)
    prompt=f"""你收到的是老板/领队提供的完整活动原始资料。直接完成两件事：
A. 提取 Activity Master（事实与业务真相）；
B. 像内容主编一样生成 C 端招募详情的动态 block 方案。
{_revision_block(direction,previous_detail,version_no)}
原始资料（保留原始上下文）：
{source.get('text','')}

媒体清单（只能引用这些 ref；kind='photo' 才是真实照片）：
{media}

图片选用铁律（2026-10-03 用户反馈）：
- 只能引用 kind='photo' 的 ref。kind='logo' 的是品牌标志 / 字标横幅 / 空白幻灯片底图 /
  地图与天气截图 / 扁平赞助商海报——它们是资料的排版残留，不是活动照片，出现在成品里就是事故。
- 品牌 logo 一律不得出现在详情页任何位置（hero / gallery / media 都不行）。活动确实需要品牌
  标识时，那是「单独上传 logo / 封面」的事，不由你从资料里挑图。
- 没有可用照片时不要硬排图，用文字把吸引力撑起来。

装备清单不要重复（2026-10-03 用户反馈）：
- 严禁输出 title 为「装备建议 / 出行清单 / 装备清单 / 携带清单 / 着装建议」之类的 info block。
  平台会在详情页下方单独渲染「出行清单」（带商城匹配、平替与会员价）。你再出一块，
  同一份清单就会在页面上出现两遍。checklist 只写进 activity_master，不要做成 block。

字段硬性契约（违反会导致前端渲染成空）：
- itinerary 必须是数组，每项形如 {{"time":"Day 1 08:00-12:00","text":"成都集合出发 → 康定城区"}}。
  逐日行程要完整展开成多条（半天到一天一条），绝不能省略成 {{"day":"Day 1","schedule":[...]}} 这类嵌套形状。
  行程是户外活动的核心事实：资料里有几天就写几天，一条都不能丢。
- checklist 必须是「具体装备品类」字符串数组（如 冲锋衣、抓绒外套、登山杖、头灯、防晒帽、保温杯、雨衣、充电宝、急救包），
  根据本次活动的地点海拔、季节、天数与难度推导；不要输出「高原适应准备」「防晒防寒装备」这类抽象分类——
  它们无法对应到商城在售装备。证件、个人药品这类非装备个人物品放在最后，最多两三项。
- 若资料含逐日行程，detail.blocks 必须包含一个 timeline block（items=[{{"time":"...","text":"..."}}]，与 itinerary 同形状）。
- 方案里的 [Slide N] / [Page N] 只是解析用的页码骨架，绝不能出现在任何 C 端字段里；
  成本、供应商报价、门店 SOP、话术禁区、内部沟通等内容属于内部资料，一律不得进入 C 端成品。

文案与排版契约（详情页的吸引力来自文字，不只是图片；2026-09-28 用户反馈「图片好看但文字没气势」）：
- 每段 narrative：eyebrow 用 2~6 字场景词（如「日出之前」「海拔4200米」「篝火燃起来时」）；
  headline 是一句有画面感的话（8~16 字），不要「关于X」「介绍X」式标题；
  body 拆成 1~3 个独立短句（用 \\n 分隔），每句 ≤30 字，写动作、感官、光线与具体数字——
  禁止「美丽的风景」「难忘的旅程」「放松身心」这类放到任何活动上都成立的空话。
- 每段 narrative 可带 pull 字段：一句独立金句（≤14 字，这一节最想让人记住的话），页面会渲染成品牌色强调行。
- 全页穿插 1~2 个 statement block（整页大字观点句，≤18 字）与至多 1 个 quote block：
  金句必须从本次活动的真实体验里长出来（地名/海拔/动作/时刻），不许放之四海皆准。
- lead 是开场引言：两句以内。第一句给画面，第二句给出发的理由。

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
      {{"type":"{BLOCK_TYPES}","eyebrow":"narrative 用场景眉题","headline":"可选","body":"可选，可用 \\n 分段","pull":"可选金句(≤14字)","text":"可选","mediaRefs":["img_01"]}}
    ]
  }}
}}

block 语义：
hero=首屏；lead=短引言；narrative=图文叙事；statement=强观点短句；media=单图/双图/拼图；gallery=图片组；facts=关键事实条；timeline=时间线；info=必要决策信息；quote=引用；divider=节奏。
任何 block 都可省略、重复、自由排序。不要为了“结构完整”机械凑章节。照片多时主动做视觉编排，照片少时不要硬凑图片。
如果没有真正事实冲突，blocking_conflicts 必须为空，直接完成成品。"""
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,images=source.get('images'))
    photos=set(_photo_refs(source))
    if gw:
        data=gw.data if isinstance(gw.data,dict) else {}
        # 模型只负责「引用」ref，媒体条目本身必须以真实上传/磁盘资料为准。
        # 直接采信模型写的 media，会让前端 ref→url 映射表为空：整页图片退化成灰色占位块、头图退化成纯色。
        master=data.get('activity_master') if isinstance(data.get('activity_master'),dict) else {}
        catalog=media_catalog(source,master)
        if catalog or not isinstance(master.get('media'),list):master['media']=catalog
        data['activity_master']=master
        if isinstance(data.get('detail'),dict):
            # 提示词已要求只用照片，这里再兜一层：模型若引用了品牌 logo / 空白图的 ref，直接剔除
            data['detail']['blocks']=_sanitize_blocks(data['detail'].get('blocks') or [],allowed_refs=photos)
        return data,gw
    data=_mock_activity(source)
    data['detail']['blocks']=_sanitize_blocks(_rotate_layout(data['detail'].get('blocks') or [],max(0,version_no-1)),allowed_refs=photos)
    return data,record_mock_usage(club_id,'detail',prompt,data)


async def regenerate_detail(club_id:int,source:dict[str,Any],master:dict[str,Any],*,direction:str='',
                            previous_detail:dict[str,Any]|None=None,version_no:int=2,
                            refresh_facts:bool=False)->tuple[dict[str,Any],GatewayResponse]:
    """重新生成活动详情（老板对第一版不满意时的「换一版」）。

    默认 refresh_facts=False：只重做叙事与排版，Activity Master 作为只读事实传入。这样换一版
    不会让标题/日期/地点/价格/人数悄悄漂移，也不会碰到团期、价格政策与报名数据。
    refresh_facts=True 时才允许模型用同一份原始资料重新提取事实（例如第一版把日期或价格读错了）。
    """
    if refresh_facts:
        data,usage=await generate_activity(club_id,source,direction=direction,
                                           previous_detail=previous_detail,version_no=version_no)
        return {'activity_master':data.get('activity_master') or master,'detail':data.get('detail') or {}},usage
    media=json.dumps(source.get('media_manifest',[]),ensure_ascii=False)
    prompt=f"""Activity Master（事实，只读；不要重新提取，也不要修改其中任何一项）：
{json.dumps(master,ensure_ascii=False)}
{_revision_block(direction,previous_detail,version_no)}
你的任务：只重新创作 C 端招募详情的 block 方案（页面叙事、图片节奏、区块顺序）。
不得新增事实：日期、地点、价格、人数、行程、费用、出行清单、领队、资质一律以 Activity Master 与原始资料为准。

原始资料（只用于取用真实细节，不是重新提取事实）：
{source.get('text','')}

媒体清单（只能引用这些 ref；kind='photo' 才是真实照片）：
{media}

图片与清单铁律（与首次生成同一标准）：
- 只能引用 kind='photo' 的 ref。kind='logo' 的是品牌标志 / 字标横幅 / 空白底图 / 截图 /
  扁平赞助商海报，一律不得出现在详情页任何位置；品牌标识只能靠「单独上传 logo / 封面」解决。
- 严禁输出 title 为「装备建议 / 出行清单 / 装备清单 / 携带清单 / 着装建议」之类的 info block：
  平台会在详情页下方单独渲染「出行清单」，重复出块会让同一份清单出现两遍。

文案与排版契约（与首次生成同一标准；吸引力来自文字，不只是图片）：
- narrative：eyebrow 用 2~6 字场景词；headline 有画面感（8~16 字）；body 拆成 1~3 个独立短句（\\n 分隔），
  每句 ≤30 字，写动作、感官、光线与具体数字，禁止空话套话；每段可带 pull 金句（≤14 字）。
- 全页穿插 1~2 个 statement 大字观点句（≤18 字）与至多 1 个 quote，金句必须从真实体验里长出来。
- 方案里的 [Slide N] / [Page N] 只是解析用的页码骨架，绝不能出现在任何 C 端字段里。

返回 JSON：
{{
  "detail":{{
    "activityUnderstanding":"",
    "coreSellingIdea":"",
    "editorialIntent":{{"opening":"","visualWeight":"","reason":""}},
    "blocks":[
      {{"type":"{BLOCK_TYPES}","eyebrow":"narrative 用场景眉题","headline":"可选","body":"可选，可用 \\n 分段","pull":"可选金句(≤14字)","text":"可选","mediaRefs":["img_01"]}}
    ]
  }}
}}

block 语义：
hero=首屏；lead=短引言；narrative=图文叙事；statement=强观点短句；media=单图/双图/拼图；gallery=图片组；facts=关键事实条；timeline=时间线；info=必要决策信息；quote=引用；divider=节奏。
任何 block 都可省略、重复、自由排序。不要为了「结构完整」机械凑章节。照片多时主动做视觉编排，照片少时不要硬凑图片。"""
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,images=source.get('images'))
    photos=set(_photo_refs(source))
    if gw:
        payload=gw.data or {}
        detail=payload.get('detail') if isinstance(payload.get('detail'),dict) else payload
        if isinstance(detail,dict):
            detail['blocks']=_sanitize_blocks(detail.get('blocks') or [],allowed_refs=photos)
        return {'activity_master':master,'detail':detail},gw
    data=_mock_activity(source)
    detail=data['detail']
    detail['blocks']=_sanitize_blocks(_rotate_layout(detail.get('blocks') or [],max(0,version_no-1)),allowed_refs=photos)
    if direction.strip(): detail['directionNote']=direction.strip()
    return {'activity_master':master,'detail':detail},record_mock_usage(club_id,'detail',prompt,data)


async def generate_channel(club_id:int,activity_master:dict[str,Any],detail:dict[str,Any],channel:str,cover_url:str|None=None)->tuple[dict[str,Any],GatewayResponse]:
    labels={'wechat':'微信公众号','xhs':'小红书','poster':'活动招募海报','recap':'活动回顾'}
    cover_note=f"活动官方封面（已上传的主视觉，优先用作首图 / 海报主图）：{cover_url}\n" if cover_url else ''
    prompt=f"""基于同一场活动，重新创作 {labels.get(channel,channel)} 原生内容。不是活动详情删减版。
{cover_note}
Activity Master（事实）：{json.dumps(activity_master,ensure_ascii=False)}
招募详情的活动理解（可参考但不要照抄结构）：{json.dumps(detail,ensure_ascii=False)}

wechat: 根据公众号阅读场景重新决定标题、开篇、图文节奏与报名收束；返回 title, summary, blocks[]。
xhs: 返回 titleOptions[], hook, body, tags[], imageSequence[]；语言更像真实平台内容，不写公文。
poster: 返回 headline, subheadline, facts[], sellingPoints[], cta, preferredMediaRefs[]。
recap: 只有提供真实 actualActivityData / 现场素材时才能叙述实际发生事件；资料不足时明确返回 needsActualData=true，不编造。
严格 JSON。"""
    gw=await generate_json(club_id=club_id,task_type=channel,system_prompt=SYSTEM,user_prompt=prompt)
    # 渠道成品（海报 / 小红书九宫格）同样只能用真实照片：品牌 logo 与空白底图不能上去
    photos={m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
            and m.get('ref') and (m.get('kind') or 'photo')=='photo'}
    if gw:
        data=gw.data or {}
        if cover_url: data['coverUrl']=cover_url
        if isinstance(data.get('blocks'),list): data['blocks']=_sanitize_blocks(data['blocks'],allowed_refs=photos)
        return data,gw
    title=activity_master.get('title','活动');idea=detail.get('coreSellingIdea','')
    gallery=[m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
             and m.get('ref') and (m.get('kind') or 'photo')=='photo']
    if channel=='wechat':data={'title':title,'summary':idea,'coverUrl':cover_url,'blocks':detail.get('blocks',[])[:6]+[{'type':'cta','headline':'查看活动详情并报名'}]}
    elif channel=='xhs':data={'titleOptions':[title,f"周末去{activity_master.get('location','山里')}，这次不赶行程"],'hook':idea,'body':idea+'\n\n具体日期、费用和报名信息见活动详情。','tags':['户外','周末去哪儿','自然'],'imageSequence':([cover_url] if cover_url else [])+gallery[:8],'coverUrl':cover_url}
    elif channel=='poster':data={'headline':title,'subheadline':idea,'facts':[activity_master.get('date',''),activity_master.get('location','')],'sellingPoints':[idea],'cta':'扫码查看详情与报名','preferredMediaRefs':([cover_url] if cover_url else [])+gallery[:1],'coverUrl':cover_url}
    else:data={'needsActualData':True,'title':f'{title}｜活动回顾','message':'请上传现场照片或领队记录后再生成真实活动回顾。'}
    return data,record_mock_usage(club_id,channel,prompt,data)
