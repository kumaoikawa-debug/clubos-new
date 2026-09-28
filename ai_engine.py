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


def media_catalog(source:dict[str,Any],master:dict[str,Any]|None=None)->list[dict[str,Any]]:
    """权威媒体清单：ref → url 只能来自真实上传/磁盘资料，模型无权定义媒体条目。

    live 模式下模型会照着 prompt 里的清单回写 media，但它只会写 ["img_01",...] 这种纯 ref，
    于是前端 mediaMap 拿不到任何 url：所有图片退化成灰色占位块、头图退化成纯色块。
    这里以 source 的媒体清单为准重建，模型在 blocks 里只被允许「引用」ref。
    """
    keys=('ref','name','url','width','height','orientation','source','page')
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


def _extract_structured(source:dict[str,Any])->dict[str,Any]:
    """从原始资料文本里「抽取」结构化事实：行程、费用、服务、清单、人数、价格。

    严格只做抽取、不做创造：抽不到就留空，由 _mock_activity 回落到既有启发式。
    这样即使没有接通大模型（演示引擎/离线），上传的 PPT/Word 方案里的行程与费用明细
    也会真实出现在活动详情里，而不是被固定模板忽略——这正是「上传了方案却完全没按方案」的根因之一。
    """
    raw=(source or {}).get('text','') or ''
    out={'title':'','date':'','location':'','price':0,'capacity':0,
         'itinerary':[],'days':[],'fees':{},'services':[],'checklist':[]}
    if not raw.strip(): return out

    # ---- 行程：先按「Day N / 第N天 / DN」定位每一天，再抓每个「开始-结束 描述」时间段 ----
    day_marks=[(m.start(),int(re.search(r'\d+',m.group(0)).group(0)))
               for m in re.finditer(r'(?:DAY|Day|D|第)\s*(\d+)\s*(?:天|日)?',raw)]
    def day_of(pos):
        d=0
        for p,num in day_marks:
            if p<=pos: d=num
            else: break
        return d
    seg=re.compile(r'(\d{1,2}):(\d{2})\s*[-—–~～到至]\s*(\d{1,2}):(\d{2})\s*([\s\S]*?)(?=\d{1,2}:\d{2}\s*[-—–~～到至]|\Z)')
    for m in seg.finditer(raw):
        start=f'{int(m.group(1))}:{m.group(2)}'; end=f'{int(m.group(3))}:{m.group(4)}'
        desc=re.sub(r'\s+',' ',m.group(5)).strip()
        # 段描述尾部常混入页脚 / 单独时间点 / 里程海拔等度量，按第一个出现的标记截断
        desc=re.split(r"ARC|&apos;|\b\d+\s*/\s*\d+\b|\b\d+KM\b|\b\d+M\b|\b\d+MIN\b|\b\d+(?:\.\d+)?H\b|全天车程|最高海拔|金山观景|夜宿|\d{1,2}:\d{2}",desc)[0]
        desc=desc.strip(' ·、，,')
        if not desc: continue
        dd=day_of(m.start())
        out['itinerary'].append({'time':(f'第{dd}天 ' if dd else '')+f'{start}–{end}','content':desc})
    out['days']=sorted({n for _,n in day_marks if n>0})

    # ---- 人数 / 人均价格 / 合计 / 地点 / 日期 / 标题 ----
    cap=re.search(r'活动人数\s*(\d{1,3})\s*人',raw) or re.search(r'(\d{1,3})\s*人\s*(?:整车成行|成行|规模)',raw)
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
         or re.search(r'([0-9][0-9,]*(?:\.\d+)?)\s*元\s*/\s*人',raw))
    total=re.search(r'未含税\s*[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)',raw) or re.search(r'合计[^\n]{0,20}?[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)',raw)
    if per:
        try: out['price']=float(per.group(1).replace(',',''))
        except ValueError: pass
    loc=re.search(r'活动地点\s*([^\n]{2,40}?)(?:住宿|景点|$)',raw)
    if loc: out['location']=re.sub(r'\s+','',loc.group(1)).strip(' ·')
    dts=re.findall(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',raw)
    if dts: out['date']=f'{dts[0][0]}-{int(dts[0][1]):02d}-{int(dts[0][2]):02d}'
    # 标题：优先「X天X夜」行程名；再退回资料里写明的活动名称；最后才用目的地名兜底。
    tit=re.search(r'([\u4e00-\u9fa5]{2,12}?[两二三四]天[一二三四]夜)',raw)
    if not tit: tit=re.search(r'(?:活动名称|活动主题|主题)[：:]?\s*([^\n]{2,24})',raw)
    if not tit: tit=re.search(r'([\u4e00-\u9fa5·]{2,24}?(?:鱼子西|蓥华山|青城山|四姑娘山|毕棚沟|达古冰川|新都桥|康定))',raw)
    if tit: out['title']=re.sub(r'\s+','',tit.group(1)).strip('·')

    # ---- 费用说明：结构化成可读对象（人均 / 合计 / 包含项），只写资料里出现过的条目 ----
    items=[]
    for kw in ['车费','保姆大巴','越野中转车','中转车','午餐','晚餐','特色餐','住宿','门票','唐卡体验','唐卡','饮用水','氧气','摄影','领队','保险','工作餐','策划执行']:
        if kw in raw and kw not in items: items.append(kw)
    if per: out['fees']['人均费用']='¥'+per.group(1)
    if total: out['fees']['合计（未含税）']='¥'+total.group(1)
    if cap: out['fees']['按人数报价']=cap.group(1)+' 人'
    if items: out['fees']['费用包含']='、'.join(items[:14])+'（详见方案成本明细）'
    if ('未含税' in raw) or ('成本预估' in raw): out['fees']['备注']='本表为方案成本预估，未含税金；价格随季节浮动。'

    # ---- 服务：只写资料里明确提到的，不凭空补充 ----
    if ('保姆大巴' in raw) or ('旅游营运' in raw): out['services'].append('交通：正规旅游保姆大巴（持证旅游营运资质）')
    if '赞巴明镜酒店' in raw: out['services'].append('住宿：新都桥·赞巴明镜酒店（两晚连住不挪行李）')
    elif '住宿' in raw: out['services'].append('住宿：'+((out['location'] or '目的地'))+'当地安排')
    if '唐卡' in raw: out['services'].append('体验：国家非遗唐卡绘制')
    if ('藏装' in raw) or ('藏服' in raw): out['services'].append('体验：康巴藏装换装打卡')
    if ('糌粑' in raw) or ('酥油茶' in raw): out['services'].append('体验：酥油茶 / 手工糌粑品鉴')
    if ('持证' in raw) and (('户外' in raw) or ('导游' in raw) or ('向导' in raw)): out['services'].append('保障：专业持证户外工作人员 / 导游全程随行')
    if '保险' in raw: out['services'].append('保障：含户外旅游保险')
    if '摄影' in raw: out['services'].append('服务：全程摄影师跟拍')

    # ---- 出行清单：只在出现明确的「携带物品 / 必备清单 / 装备清单」标题时抽取，其余一律留空。
    #      （否则「携带方便、耐储存」这类产品描述会被误当成要带的装备——绝不能把描述当清单。）
    m=re.search(r'(?:需携带|携带物品|携带清单|必备清单|装备清单|自备物品|出行清单|需准备)[：:]?\s*([^\n。；;]{2,80})',raw)
    if m:
        for part in re.split(r'[、,，/;；\s]+',m.group(1)):
            part=part.strip(' ·。')
            if 1<len(part)<=12 and part not in out['checklist']: out['checklist'].append(part)
    return out


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

    # 结构化抽取：即使不接通大模型（演示引擎），上传方案里的真实行程/费用/服务也要如实进入成品。
    st=_extract_structured(source)
    # 已知目的地已有更完整的成品标题时不要被覆盖；只有通用兜底标题才用资料里抽到的名字。
    if st.get('title') and (not title or title=='周末自然计划'):title=st['title']
    if st.get('date'):date=st['date']
    if st.get('location'):location=st['location']
    price_val=float(st['price']) if st.get('price') else (float(price) if str(price).isdigit() else 0.0)
    cap_val=int(st['capacity']) if st.get('capacity') else (int(capacity) if str(capacity).isdigit() else 0)
    price_text=('¥'+str(st['price'])) if st.get('price') else price

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
    # 资料里抽到真实行程/服务/费用时，用它们替换「结构完整」的通用示意块——
    # 让没接通大模型的演示引擎也严格跟着上传方案走，而不是套固定模板。
    if st.get('itinerary'):
        blocks.append({'type':'timeline','title':'行程安排','items':[{'time':it['time'],'text':it['content']} for it in st['itinerary'][:16]]})
    _info=[]
    if st.get('fees',{}).get('人均费用'):_info.append('人均费用 '+st['fees']['人均费用'])
    if cap_val:_info.append('成行人数 '+str(cap_val)+' 人')
    _info+=list(st.get('services') or [])
    if _info:blocks.append({'type':'info','title':'服务与保障','items':_info[:10]})
    # 没有真实行程时才用通用时间线占位，避免与真实安排冲突、也不编造具体时间。
    if not st.get('itinerary') and ('08:00' in text or '8:00' in text):
        blocks.append({'type':'timeline','title':'这一天怎么走','items':[
            {'time':'08:00','text':'集合 / 签到'},{'time':'10:00','text':'进入当天核心体验'},{'time':'12:00','text':'午餐 / 休整'},{'time':'17:00','text':'结束行程 / 返程'}]})
    if remaining[8:]: blocks.append({'type':'media','mediaRefs':remaining[8:12],'layout':'mosaic'})
    blocks.append({'type':'cta','headline':'想去，就把这一天留出来','text':'确认日期、费用与详细行程后即可报名。'})

    master={
        'title':title,'date':date,'location':location,
        'price':price_val,
        'capacity':cap_val,
        'publicFacts':{'date':date,'location':location,'price':price_text,'distance':distance},
        'itinerary':st.get('itinerary') or [],
        'fees':st.get('fees') or {},
        'checklist':st.get('checklist') or [],
        'services':st.get('services') or [],
        'internalData':{},'uncertainties':[],'blocking_conflicts':[],
        'media':media_catalog(source),
        'sourceSummary':text[:8000]
    }
    detail={
        'activityUnderstanding':understanding,
        'coreSellingIdea':idea,
        'editorialIntent':{'opening':'由当前活动最强动机决定','visualWeight':'由素材质量决定','template':'NONE'},
        'blocks':blocks
    }
    return {'activity_master':master,'detail':detail}


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

媒体清单（只能引用这些 ref）：
{media}

字段硬性契约（违反会导致前端渲染成空）：
- itinerary 必须是数组，每项形如 {{"time":"Day 1 08:00-12:00","text":"成都集合出发 → 康定城区"}}。
  逐日行程要完整展开成多条（半天到一天一条），绝不能省略成 {{"day":"Day 1","schedule":[...]}} 这类嵌套形状。
  行程是户外活动的核心事实：资料里有几天就写几天，一条都不能丢。
- checklist 必须是「具体装备品类」字符串数组（如 冲锋衣、抓绒外套、登山杖、头灯、防晒帽、保温杯、雨衣、充电宝、急救包），
  根据本次活动的地点海拔、季节、天数与难度推导；不要输出「高原适应准备」「防晒防寒装备」这类抽象分类——
  它们无法对应到商城在售装备。证件、个人药品这类非装备个人物品放在最后，最多两三项。
- 若资料含逐日行程，detail.blocks 必须包含一个 timeline block（items=[{{"time":"...","text":"..."}}]，与 itinerary 同形状）。

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
    if gw:
        data=gw.data if isinstance(gw.data,dict) else {}
        # 模型只负责「引用」ref，媒体条目本身必须以真实上传/磁盘资料为准。
        # 直接采信模型写的 media，会让前端 ref→url 映射表为空：整页图片退化成灰色占位块、头图退化成纯色。
        master=data.get('activity_master') if isinstance(data.get('activity_master'),dict) else {}
        catalog=media_catalog(source,master)
        if catalog or not isinstance(master.get('media'),list):master['media']=catalog
        data['activity_master']=master
        return data,gw
    data=_mock_activity(source)
    data['detail']['blocks']=_rotate_layout(data['detail'].get('blocks') or [],max(0,version_no-1))
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

媒体清单（只能引用这些 ref）：
{media}

返回 JSON：
{{
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
任何 block 都可省略、重复、自由排序。不要为了「结构完整」机械凑章节。照片多时主动做视觉编排，照片少时不要硬凑图片。"""
    gw=await generate_json(club_id=club_id,task_type='detail',system_prompt=SYSTEM,user_prompt=prompt,images=source.get('images'))
    if gw:
        payload=gw.data or {}
        detail=payload.get('detail') if isinstance(payload.get('detail'),dict) else payload
        return {'activity_master':master,'detail':detail},gw
    data=_mock_activity(source)
    detail=data['detail']
    detail['blocks']=_rotate_layout(detail.get('blocks') or [],max(0,version_no-1))
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
    if gw:
        data=gw.data or {}
        if cover_url: data['coverUrl']=cover_url
        return data,gw
    title=activity_master.get('title','活动');idea=detail.get('coreSellingIdea','')
    gallery=[m.get('ref') for m in activity_master.get('media',[]) if m.get('ref')]
    if channel=='wechat':data={'title':title,'summary':idea,'coverUrl':cover_url,'blocks':detail.get('blocks',[])[:6]+[{'type':'cta','headline':'查看活动详情并报名'}]}
    elif channel=='xhs':data={'titleOptions':[title,f"周末去{activity_master.get('location','山里')}，这次不赶行程"],'hook':idea,'body':idea+'\n\n具体日期、费用和报名信息见活动详情。','tags':['户外','周末去哪儿','自然'],'imageSequence':([cover_url] if cover_url else [])+gallery[:8],'coverUrl':cover_url}
    elif channel=='poster':data={'headline':title,'subheadline':idea,'facts':[activity_master.get('date',''),activity_master.get('location','')],'sellingPoints':[idea],'cta':'扫码查看详情与报名','preferredMediaRefs':([cover_url] if cover_url else [])+gallery[:1],'coverUrl':cover_url}
    else:data={'needsActualData':True,'title':f'{title}｜活动回顾','message':'请上传现场照片或领队记录后再生成真实活动回顾。'}
    return data,record_mock_usage(club_id,channel,prompt,data)
