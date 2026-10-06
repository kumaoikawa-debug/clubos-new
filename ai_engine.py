from __future__ import annotations
import json,re
from typing import Any
from ai_gateway import generate_json, record_mock_usage, GatewayResponse
from cost_guard import (scrub_cost_text, scrub_cost_data, sanitize_for_frontend, is_cost_row,
                        is_cost_key, has_cost_context, master_has_cost_evidence)

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

# 文案契约（2026-10-07 重写）。
# 旧契约要求「eyebrow 用 2~6 字场景词 / body 拆成一行一个 ≤30 字短句 / 每段都配一句对仗 pull」，
# 结果是每一节长得一模一样：场景小标题 + 四行诗 + 「A，B」式金句，一眼就是 AI 写的。
# 新契约反过来把这三条列为禁令，改为要求连贯段落、信息型小标题、以及「只有这场活动才有」的具体细节。
_WRITING_CONTRACT = """文案与排版契约（2026-10-07 用户反馈「生成的内容都很差、一眼就是 AI 写的」后重写）：

★★ 总纲：你不是在填模板，你是在写一篇让人读完就想报名的长文。读起来必须像一位熟悉这条线的
户外编辑亲手写的——有具体细节、有判断、有节奏变化。以下「AI 腔」特征必须彻底消灭：

- 禁令 A｜场景词小标题：不要用「日落之前」「夜幕降临」「清晨醒来」「藏式手作」「换装时刻」
  「落脚之处」「出发时刻」这类 2~6 字泛化场景词当小节标题。标题必须给出**信息或判断**。
  反例：`日落之前`、`攀上鱼子西，迎接金色的神山`（空洞无信息）。
  正例：`三座神山同框的 30 分钟，值得专程奔赴`、`3.5 小时，画一幅真正属于你的唐卡`、
  `赞巴明镜酒店：夯土墙里的现代侘寂`、`打酥油茶、捏糌粑——高原生存智慧，亲手还原`。

- 禁令 B｜一行一句的排版诗：body 绝对不要写成
  「海拔4200米的观景平台\\n三座神山同框出现\\n阳光穿透云层，将雪山染成熔金」这种一行一个短句。
  要写成**连贯段落**：每段 2~4 句、60~180 字，句子里正常使用逗号、顿号、破折号与分号。
  段与段之间才换行，一节最多 2 段。短句可以有，但必须嵌在长句的节奏里，而不是一句一行。

- 禁令 C｜对仗金句：pull 金句是**可选**的，全篇最多 2 处，且不许都用「A，B」对仗格式。
  反例（六句一个模子，绝对不能这样）：「日照金山，照进心里」「味觉与声音的高原」
  「画一幅，带走一段时光」「舌尖上的康巴文化」「拍一张，留在记忆里」「雪山在窗，心在屋」。
  宁可一句金句都不写，也不要凑。

- 禁令 D｜空话套话：禁止「美丽的风景」「难忘的旅程」「放松身心」「心旷神怡」「不虚此行」
  「远离喧嚣」「值得拥有」「人生必去」「让身心都得到治愈」「本次活动的亮点在于」。
  把每一个空话换成只有这场活动才成立的事实。

- 禁令 E｜每节同构：不要让每一节都是「小标题 + 一段描述 + 一张图」。段落长短、有没有配图、
  配几张图，都应该跟着内容走：有的章节两句话就够，有的可以写满一段；有的配图，有的不配。

- 禁令 F｜句式雷同（最容易露馅的一条）：**「不是 X，是 Y」这一类否定对仗句，全文最多用 1 次。**
  实测这是最强惯性：资料给得越足，模型越会在每一节都写一次，连读六节一眼就是 AI。
  反例：「这不是打卡式旅行，是身体攀向光」「不是滤镜，不靠修图」「这不是表演，是松弛的共在」
  「不是临摹，是遵循度量经；不是速成，是…」「唐卡不只是绘画，更是修行」「它不是一个景点，而是一个平台」。
  **发现自己正在写这种句子，直接改成陈述句**：
     ✗「唐卡不只是绘画，更是修行」→ ✓「唐卡绘制是一次修行：必须遵从《佛说造像度量经》，比例不能随意改动。」
     ✗「它不是一个景点，而是一个 360° 平台」→ ✓「鱼子西是一块海拔 4200 米的观景平台，能同时看到三座神山。」
  同理，不要让每节都以「在海拔 4200 米，…」或「当…的时候，…」起头；相邻小节的开场方式必须不同。

- 禁令 G｜给资料里没有的东西添细节：资料写「康巴藏装，款式任选，自由拍摄」，你就只能写到这个程度，
  **不要自己补「珊瑚项链、松石耳坠、镶边氆氇裙、盘羊角纹马甲」**；资料没写的配饰、菜品、材料、
  器材、品牌、产地，一律不许出现。写不出出处的具体物件，就不写——宁可只写资料里有的那几件。

★★ 合格的段落长这样（模仿这种信息密度与句子节奏。**〔〕里的只是占位符**，你必须换成
  本次原始资料里**真实存在的**数字，一个都不许照抄，更不许把占位符当成事实来源）：
  「木桶上下抽打〔资料里写明的次数〕下，让酥油与浓茶彻底乳化；青稞炒香后石磨成粉，加入奶渣、
   温茶、一小块酥油。揉成团，咬一口，微咸、微酸、微甜、微韧——这是牧民走一天山路的能量。」
  「〔资料里写明的开业年份〕年开业的藏式隐奢空间，没有浮夸雕饰，只有厚实夯土墙、低垂暖光、
   窗外整面雪山。两晚连住，行李不动，你只需推开窗，就能把神山框进日常。」
  → 共同点：有数字、有专名、有动作、有感官、有判断；长短句交错；没有一句能搬到别的活动上去。

★★ 细节的边界（与上一条同等重要，「挖细节」绝不等于「编细节」）：
  上面要求的具体，**只能来自原始资料里已经写过的内容**。以下全部属于编造，出现即不合格：
  - 资料没写的机构名 / 资质名 / 证书名（例：「甘孜州非遗传承中心」「中国登山协会高山向导证」
    「文旅局签发的旅游营运资质」——只有资料写了才允许写）；
  - 资料没写的材料 / 品类 / 工艺参数（例：「青金石蓝」「24K 金粉」「火漆印」「有编号」）；
  - 资料没写的经验 / 荣誉 / 数值（例：「驾龄 12 年」「连续 6 年零事故」「气压约 60kPa」
    「核载 26 人」「二楼静修室」「塔公乡妇女合作社」）；
  - 资料没写的药品 / 装备配给（例：「乙酰唑胺」「血氧仪」「每人 1 瓶氧气」）；
  - 资料没写的人数、名额、剩余席位（例「仅剩 12 席」——除非资料明确写了）。
  你可以做的是**把资料里已经写了的细节写得更好**：换更有画面感的动词、调整句子节奏、
  点明它为什么重要。资料写「木杆反复上下抽打」→ 你写「用长木杆反复上下抽打，直到乳化成
  温润的奶霜状」是对的（「奶霜状」是表达，属于你可发挥的空间）；但「300 下」如果资料没写，
  就不许出现。**任何你在资料里找不到出处的事实，都写成你在资料里能看到的那个版本。**

★ 资料是原料，不是成品：**不要照抄原始资料里的句子**。方案 PPT 里的句子是「资料语言」
  （名词堆叠、短句罗列、无主语），不是你该交付的文章。事实（数字 / 专名 / 工艺 / 时间）
  必须与资料完全一致，但**表达必须是你自己的**：重组语序、换更准确的动词、把 PPT 的碎片
  连成有呼吸的长句、点明它为什么值得专程去一趟。
  自查标准：把你写的段落和资料原句并排看——如果几乎一样，说明你只是在复制，没在写。
  同样，也不要复制你自己上一段的话：相邻小节不要用相同的开场方式，也不要重复同一个形容词。

★ 每一节至少要含 1 个「只有这场活动才有」的具体事实：数字（海拔 / 时长 / 公里数 / 人数 / 年份）、
  专有名词（地名 / 酒店名 / 机构名 / 经文名 / 菜品名）、具体动作或机制（怎么抽打、怎么上色、几点抵达）。
  一个章节里挖不到具体事实，就说明这节不该写。

★ 小节标题（headline）：8~22 字，写**判断句或信息句**，不是词组堆叠。数字和专名是好朋友。
★ eyebrow 字段可选，只在确实需要场景锚点时才用，且不要连续两节都用。
★ 全页穿插 1~2 个 statement（整页大字观点句，≤18 字）与至多 1 个 quote；金句必须从本次活动的
  真实体验里长出来（地名 / 海拔 / 动作 / 时刻），不许放之四海皆准。
★ lead 是开场引言：2~3 句，第一句给画面、最后一句给出发的理由。不要写成「本次活动旨在…」。"""


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
# 事实回检（2026-10-04 用户反馈「把方案包装成有吸引力的活动详情」后加的出口闸门）：
# 模型为了把文案写得好看，会顺手改写专名 —— 实测资料写「icebreaker」，成品音译成「冰破」。
# 顾客会认错品牌、会照着错地名导航，属事故级。提示词里已下保真铁律，这里再加一道出口回检：
#   ① 品牌名音译 → 直接还原成资料里的原始写法（映射明确，可自动修，已单测）；
#   ② 成品里出现、但资料里**查无此名**的地名 → 不猜、不替换，写进 uncertainties 让运营核对。
# ⚠ 事实澄清（别再被之前的错误结论带偏）：兴福寺方案详情里出现的「草寺庙」**不是模型编的**，
#   是 Slide 9 行程表格原文就写着「到达草寺庙后森林区域午餐」；模型忠实照抄了。
#   那属于「原文自己前后不一致」，正则判定不可靠（详见 _GEO_HEAD_STOP 下方说明），
#   已改为要求模型写进 uncertainties，不在这里判。
# ---------------------------------------------------------------------------
# 模型实测过的音译/意译别名 → 资料里的原始写法。命中就整体替换，不做模糊匹配。
_BRAND_ALIASES={'冰破':'icebreaker','破冰':'icebreaker','破冰者':'icebreaker',
                '冰破者':'icebreaker','始祖鸟':"ARC'TERYX",'乐斯菲斯':'TheNorthFace'}
# 地名后缀：用于从文本里抓「疑似地名」。只用来**发现**，不用来判定。
_GEO_TAIL=('寺','庙','山','峰','沟','湖','镇','村','坪','谷','坡','坝','溪','桥','关','寨')
# 只取「1 个汉字 + 后缀」的两字词做子串比对。不要贪心取 5 字前缀——中文没有词边界，
# 实测 `[\u4e00-\u9fa5]{1,5}(寺|庙)` 会把动词吞进去，把「徒步至草寺庙」整段当成地名，
# 于是真的「草寺」反而没被单独认出来。子串比对更稳：只要原文里出现过就不算编造。
_GEO_RE=re.compile(r'[\u4e00-\u9fa5](?:'+'|'.join(_GEO_TAIL)+')')
# 通用词：这些「汉字+后缀」在任何活动里都会出现，不是专名，不能报。
_GENERIC_GEO={'寺庙','山林','山河','山水','山村','山谷','山坡','溪流','桥梁','城镇','山峰',
              '湖畔','海边','江边','河岸','森林','公园','景区','营地','山野','山间','田间',
              '寺院','山地','山道','河道','沟谷','坡地','村镇','寨子','关隘','坪地'}
# 这些字几乎不会是专名首字，出现在「汉字+后缀」前面基本都是量词/修饰语/动词。
# 实测假阳性①：「有人夹起一片山药」里的「片山」被当成编造的地名报了出来。
# 实测假阳性②：「穿过寺庙与山林，沿着山道走到村镇」里的「与山/着山/到村/过寺」。
# 汉语没有词边界，动词 + 后缀就会拼出伪专名 —— 只能靠首字黑名单挡。
_GEO_HEAD_STOP=set('片座条道个处块段些这那整满半每各前后上下里外中间边旁对同全名位次'
                   '种群排列张幅点层步米公小大老新高深远近多少一二三四五六七八九十'
                   '与到着过走穿从向往在由经达去来回归出入沿顺登爬翻越看望有无是的了和'
                   '跟及或并而把被让使给自至又再还就才只都也很太更最行进抵绕靠邻近距奔'
                   '赴览游攀hofer是能会将可使须应被所对该其们时分秒年天周月'
                   '散漫环绕驱弥消吹笼锁压')
# 这些字是「散/漫/环/绕…」这类动词首字，几乎不会是专名首字；
# 它们 + 地理后缀只会拼出动词短语（驱散山间、漫山遍野、环山公路、绕山而行），不是地名。
# 实测假阳性③：「一锅暖意，驱散山间微寒」里的「散山」被当成编造地名报了出来。
_GEO_RIGHT_RELATIONAL=set('间中上下前后里畔边脚腰顶口头尾侧心面底内外东西南北端梢陲沿际')
# 地理后缀后紧跟这些「关系后缀」→ 整个「汉字+后缀」是方位短语而非专名。
# 例：「驱散山间微寒」→ 山后是「间」→ 散山是动词短语；「寺中/湖畔/村口/谷底/坡顶/桥下/关外/寨内」同理。
# 这是比首字黑名单更通用的护栏：只要后缀处于方位短语里，就绝不可能是独立地名。


# ---------------------------------------------------------------------------
# 「原文里的专名前后不一致」为什么**不做**正则自动检测（试过、已回退，别再走一遍）：
# 兴福寺方案实测：Slide 8「石笋寺-兴福寺」、Slide 9 表格「到达草寺庙后森林区域午餐」、
# Slide 2 又写兴福寺曾名「石庙子」。一台徒步活动同时有起点寺、终点寺、古称是**正常**的。
#   ① 第一版按「同一后缀下出现多个专名 = 笔误」判定 → 把正常的「石笋寺→兴福寺」判成笔误；
#   ② 第二版改成「列出原文所有专名让运营过目」→ 汉语没有词边界，正则把「午间在寺」
#      「起一片山」「协会高山」「客户沟」（来自"客户沟通"）都当专名列了出来，噪声比信号多。
# 根因：**专名识别需要分词/语义，正则做不到**。这种「是不是同一个地方的两种写法」是
# 判断题，交给模型（提示词里已要求它把原文可疑专名写进 uncertainties），不要用阈值硬猜。
# 保留确定性闸门只做两件有把握的事：品牌名还原（映射明确）、成品里查无出处的专名告警。
# ---------------------------------------------------------------------------


def restore_brand_names(text:str,source_text:str)->str:
    """把模型音译/意译的品牌名还原成资料里的原始写法。"""
    if not text or not source_text: return text
    out=str(text)
    # ★ 路径 / URL 不是文案，必须原样返回。上传的图片文件名里就带品牌中文名
    #   （…/extracted/2026始祖鸟高客_四川新都桥鱼子西方案_最新__image2.jpg），
    #   一旦被下面的映射改成「2026ARC'TERYX高客_…」，磁盘上根本没有这个文件，
    #   整个活动详情页的图片会全部 404（2026-10-07 实测事故，且每次重新生成都会复发）。
    if out.startswith(('/','http://','https://','data:','blob:')): return out
    for alias,canonical in _BRAND_ALIASES.items():
        if alias in out and canonical.lower() in source_text.lower():
            out=out.replace(alias,canonical)
    return out


def hallucinated_places(text:str,source_text:str)->list[str]:
    """成品里出现、但原始资料里查无此名的地名/寺庙名 —— 模型编造或改写过的。

    判据：成品里的「汉字+地理后缀」两字词，只要**不是原始资料的连续子串**，且不是
    「寺庙/山林」这类通用词，就认为是模型编出来的。子串比对而不是分词——中文没有
    词边界，靠正则切词会把「徒步至草寺庙」整段当成一个地名，反而漏掉真凶「草寺」。
    """
    src=str(source_text or '')
    if not src: return []
    t=str(text or '')
    out=[]
    for term in sorted(set(_GEO_RE.findall(t))):
        if term in _GENERIC_GEO: continue
        if term[0] in _GEO_HEAD_STOP: continue   # 「一片山药」里的「片山」不是地名
        if term in src: continue          # 原文出现过（子串即可）就不算编造
        # 地理后缀后紧跟关系后缀（间/中/上/下/畔/口/底…）→ 是方位短语不是专名。
        # 「驱散山间微寒」→ 山后是「间」→ 散山是动词短语，不能报编造地名。
        i=t.find(term)
        if i>=0 and i+len(term)<len(t) and t[i+len(term)] in _GEO_RIGHT_RELATIONAL:
            continue
        out.append(term)
    return out


def _map_strings(node:Any,fn)->Any:
    if isinstance(node,str): return fn(node)
    if isinstance(node,dict): return {k:_map_strings(v,fn) for k,v in node.items()}
    if isinstance(node,list): return [_map_strings(v,fn) for v in node]
    return node


def apply_cost_guard(data:dict[str,Any])->bool:
    """出口成本闸门：把成本/报价数据从成品里彻底摘掉（含价格归零），返回是否清洗过。

    为什么需要这一层：解析期已经不再把成本行喂给模型（document_parser.redact_cost_text），
    但 live 模式下模型仍可能自己写出价格、或把「人均 3806 元」算进叙事文案；而库里
    **已经存在**的老活动更是带着完整成本表（用户截图里那张「费用说明 PRICE」卡片就是）。
    所以成品在离开引擎前必须再过一遍 —— 见 cost_guard.sanitize_for_frontend。
    """
    if not isinstance(data,dict): return False
    mast=data.get('activity_master'); det=data.get('detail')
    if not isinstance(mast,dict): return False
    m,d,changed,_cd=sanitize_for_frontend(mast,det)
    data['activity_master']=m
    if isinstance(det,dict) and isinstance(d,dict): data['detail']=d
    return changed


def apply_fact_guard(data:dict[str,Any],source_text:str)->list[str]:
    """出口事实回检：① 品牌名音译还原成资料原始写法；② 资料里查无此名的地名收集成告警。

    返回可疑地名列表，并同步写进 master['uncertainties']（俱乐部端可见，便于运营核对）。
    internalData 是内部经营资料，**不做任何改写**，避免把运营口径改坏。
    """
    # ★ 成本闸门先跑，且不受 source_text 是否为空影响：成本数据不许进入成品的判定
    #   与被上传资料的完整性无关（见 cost_guard 模块文首）。
    apply_cost_guard(data)
    src=str(source_text or '')
    if not src or not isinstance(data,dict): return []
    def fix(t:str)->str: return restore_brand_names(t,src)
    det=data.get('detail'); mast=data.get('activity_master')
    if isinstance(det,dict): data['detail']=_map_strings(det,fix)
    if isinstance(mast,dict):
        for k in list(mast.keys()):
            # internalData 是运营口径；media 是磁盘文件的映射（ref→url），两者都不是文案，
            # 做品牌名替换只会把 url 改坏（见 restore_brand_names 的说明）。
            if k in ('internalData','media'): continue
            mast[k]=_map_strings(mast[k],fix)
    # 收集可疑地名（只看 C 端可见部分）
    parts=[]
    def collect(n):
        if isinstance(n,str): parts.append(n)
        elif isinstance(n,dict):
            for kk,vv in n.items():
                if kk=='internalData': continue
                collect(vv)
        elif isinstance(n,list):
            for vv in n: collect(vv)
    collect(data.get('detail') or {})
    if isinstance(mast,dict): collect({k:v for k,v in mast.items() if k!='internalData'})
    suspects=set()
    for t in parts: suspects.update(hallucinated_places(t,src))
    out=sorted(suspects)
    notes=[f'地名「{s}」在原始方案里查无此名，疑似改写/编造，请核对' for s in out]
    # 「原文自己前后写法不一致」不在这里判（正则做不到专名识别，试过两版都退回，见文首说明）。
    # 那件事由模型在 uncertainties 里写出来 —— 提示词已明确要求。
    if notes and isinstance(mast,dict):
        un=mast.get('uncertainties')
        if not isinstance(un,list): un=[]
        for note in notes:
            if note not in un: un.append(note)
        mast['uncertainties']=un
    return out


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


def _public_price(raw:str)->str:
    """只在「对外报价」语境里取价格 —— 成本表里的数字不当售价。

    2026-10-06 用户要求：方案里的成本数据前端一律不显示。此前这里把 `人均费用` 也当售价抓
    （一份始祖鸟高客方案的成本是 ¥3,806.55/人 = 76,131 ÷ 20，抓成 price 后 C 端卡片直接
    按成本价开卖）。现在逐行判定：命中成本行（人均费用/合计/未含税/单价…）就跳过，
    继续往下找真正的公开价格（售价 / 会员价 / 每人 288 元 / 498 元每次）。
    整份资料没有任何公开价格时返回空串，价格留空由俱乐部自己定。
    """
    for ln in str(raw or '').split('\n'):
        if is_cost_row(ln, has_cost_context(raw)):
            continue
        for pat in _PRICE_PATTERNS:
            m=pat.search(ln)
            if m:
                return m.group(1)
    return ''


_PRICE_PATTERNS=(
    # 「售价 ¥498」「会员价 498」「每人 288 元」「人均 498」
    re.compile(r'(?:售价|价格|会员价|新客价|优惠价|每人|人均)[^\n\d]{0,8}?([0-9][0-9,]*(?:\.[0-9]+)?)'),
    # 套餐式报价：「498 元/次」「298/人」
    re.compile(r'([0-9][0-9,]{1,5})\s*元?\s*/\s*(?:次|人|位|场|份)'),
)


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
    # ---- 价格：只在「对外报价」语境里取，成本表里的数字一律不当售价 ----
    per=_public_price(raw)
    if per:
        try: out['price']=float(per.replace(',',''))
        except ValueError: pass
    out['location']=_extract_location(raw)
    dts=re.findall(r'(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?',raw)
    if dts: out['date']=f'{dts[0][0]}-{int(dts[0][1]):02d}-{int(dts[0][2]):02d}'
    # 标题：优先「X天X夜」行程名；再退回资料里写明的活动名称。
    # 不再用「目的地名兜底」——那会把行政地名当标题，交给 _mock_activity 组装更干净。
    tit=re.search(r'([\u4e00-\u9fa5]{2,12}?[两二三四]天[一二三四]夜)',raw)
    if not tit: tit=re.search(r'(?:活动名称|活动主题|主题)[：:]?\s*([^\n]{2,24})',raw)
    if tit: out['title']=_clean(tit.group(1)).strip('·')

    # ---- 费用说明：只写顾客该知道的项，**只写项名、不写金额** ----
    # 2026-10-06 用户要求：方案里的成本数据前端一律不显示。此前这里把成本表抓成了 fees：
    # 人均费用（= 合计 ÷ 人数）、合计（未含税）、按人数报价、以及「本表为方案成本预估」的备注，
    # 前端再渲染成「费用说明 PRICE」卡片 —— 等于把俱乐部的成本底价和利润结构摊给顾客看。
    # 现在只留「费用包含」这一项服务范围（车费/门票/氧气/摄影…），价格与成本一概不写。
    items=[]
    for kw in ['车费','保姆大巴','越野中转车','中转车','午餐','晚餐','特色餐','住宿','门票','唐卡体验','唐卡',
               '饮用水','氧气','摄影','领队','保险','工作餐']:
        if kw in raw and kw not in items: items.append(kw)
    if items: out['fees']['费用包含']='、'.join(items[:14])

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
    # 成行人数是活动事实、可以对外；「人均费用」是成本推导出来的内部单价，一律不写
    # （2026-10-06 用户要求：成本数据前端不显示。此前这里把 ¥3,806.55 印进了「服务与保障」）。
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
    data={'activity_master':master,'detail':detail}
    apply_cost_guard(data)   # 成本闸门：mock 也不豁免（成品的最后一道门）
    return data


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

★★ 事实保真铁律（2026-10-04 用户反馈：包装要好看，但**不许编**）：
- 专有名词一律逐字照抄原文，禁止音译、意译、美化或凭空生成。**资料里没出现过的地名/
  寺庙名/山峰名/路线名，一个字都不许出现在成品里** —— 编出来的地名顾客会按它导航，是事故。
  原文怎么写就怎么写，哪怕你自己觉得它写错了：行程表格写「草寺庙」就照抄「草寺庙」，
  不许擅自改成别处出现过的「石笋寺」。
- 品牌名保留原始写法。资料写「icebreaker」就写「icebreaker」，**不要音译成「冰破」、
  意译成「破冰者」**，也不要随意大小写。品牌名写错是商务事故。
- 数字（距离/累计爬升/时长/价格/积分/人数/海拔）必须与原文**完全一致**，不得四舍五入、
  不得换算、不得凭印象补。原文没有的数字就不要写在 facts/timeline 里。
- 行程地点若原文只给了「A-B」两个端点（如「石笋寺-兴福寺」），就照抄这两个端点，
  不要自行推导中间站点。
- 若原文**自己前后不一致**（同一份资料里 Slide 8 写「石笋寺」、行程表格里又写「草寺庙」），
  你照抄不改，同时把疑虑写进 activity_master.uncertainties，例如：
  「原文对同一地点出现两种写法：石笋寺 / 草寺庙，请确认正式名称」。没有疑虑就留空数组。
  你不负责判定哪个写法对，只负责把矛盾原样交还给运营。
- 标题：从方案里提炼一个**有吸引力的活动名**（≤20 字，如「兴福寺徒步 × 森林颂钵」），
  **不要照抄用户提的需求描述**（那种长句子是给 AI 看的，不是给顾客看的标题）。

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
- 方案里的 [Slide N] / [Page N] 只是解析用的页码骨架，绝不能出现在任何 C 端字段里。

★★ 成本数据铁律（2026-10-06 用户反馈，最高优先级）：
- 方案里的「活动费用明细 / COST」页是俱乐部的**成本底价**（单价、小计、合计（未含税）、人均费用、
  策划执行 10%、税费、毛利…）。这类数字**一个都不许出现在成品里** —— 不在 fees、不在正文、
  不在 narrative/statement/info/facts 的任何一句话里，也不要改写成「人均约 3800 元」「成本约 7.6 万」
  这种模糊说法。顾客看到成本价等于把利润结构摊在桌上。
- fees 只允许写「费用包含」这一项（顾客该知道含什么：车费、门票、氧气、摄影…），
  **只写项名、不写金额**；禁止输出 人均费用 / 合计 / 合计（未含税）/ 单价 / 小计 / 按人数报价 /
  策划执行 / 备注 这类键。
- price 只在方案里出现**明确的对外报价**（售价 / 报名费 / 会员价 / 每人 288 元）时才填；
  资料里只有成本明细页时，price 留 0，由俱乐部自己定价 —— 绝不要把成本均价当对外售价填进去。
- 成本、供应商报价、门店 SOP、话术禁区、内部沟通等内容属于内部资料，一律不得进入 C 端成品。

{_WRITING_CONTRACT}

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
        # 事实回检：品牌名音译还原 + 编造地名告警（模型包装文案时会顺手改专名）
        apply_fact_guard(data,str(source.get('text') or ''))
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

{_WRITING_CONTRACT}
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
        # 事实回检（换一版同样会改写专名，不能只在首次生成时守）
        apply_fact_guard({'activity_master':master if isinstance(master,dict) else {},'detail':detail},
                         str(source.get('text') or ''))
        return {'activity_master':master,'detail':detail},gw
    data=_mock_activity(source)
    detail=data['detail']
    detail['blocks']=_sanitize_blocks(_rotate_layout(detail.get('blocks') or [],max(0,version_no-1)),allowed_refs=photos)
    if direction.strip(): detail['directionNote']=direction.strip()
    return {'activity_master':master,'detail':detail},record_mock_usage(club_id,'detail',prompt,data)


def normalize_channel_blocks(data:dict[str,Any])->dict[str,Any]:
    """把模型返回的各种「形状」归一到 blocks 数组。

    为什么需要：前端只认 data.blocks。实测 qwen-max 会自作主张返回
    {"title","lead":{...},"narrative":[{headline,body,mediaRefs}...],"facts":{...},"closing":{quote,cta}}，
    形状一变，blocks 为空 → 内容中心显示「已生成」但预览整篇空白，用户只会说「又坏了」。
    与其指望提示词约束住模型，不如在出口做一次结构归一化，把内容救回来。
    """
    if not isinstance(data,dict): return data
    blk=data.get('blocks')
    if isinstance(blk,list) and blk: return data
    out=[]
    def add(btype,src):
        if src is None: return
        if isinstance(src,list):
            for it in src: add(btype,it)
            return
        b={'type':btype}
        if isinstance(src,str):
            t=src.strip()
            if not t: return
            b['text']=t
        elif isinstance(src,dict):
            for k in ('eyebrow','headline','title','subtitle','body','text','pull','summary'):
                v=src.get(k)
                if isinstance(v,str) and v.strip(): b[k]=v.strip()
            for k in ('mediaRefs','items'):
                v=src.get(k)
                if isinstance(v,list) and v: b[k]=v
            if not any(k in b for k in ('headline','title','body','text','items')): return
        else:
            return
        out.append(b)
    add('lead',data.get('lead') or data.get('intro') or data.get('opening'))
    add('narrative',data.get('narrative') or data.get('narratives')
        or data.get('sections') or data.get('story') or data.get('paragraphs'))
    add('statement',data.get('statement') or data.get('statements'))
    add('gallery',data.get('gallery') or data.get('images'))
    add('facts',data.get('facts') or data.get('info'))
    add('quote',data.get('quote'))
    for key in ('timeline',):
        add('timeline',data.get(key))
    closing=data.get('closing')
    if isinstance(closing,dict):
        add('quote',closing.get('quote'))
        add('cta',closing.get('cta') or closing.get('action'))
    else:
        add('cta',closing or data.get('cta'))
    if out:
        data['blocks']=out
        # 顶层摘要：前端标题下方的导语，缺了就从前几段里挑一句
        if not data.get('summary'):
            for b in out:
                if b['type'] in ('lead','narrative') and b.get('text'):
                    data['summary']=b['text'][:60]; break
    return data


async def generate_channel(club_id:int,activity_master:dict[str,Any],detail:dict[str,Any],channel:str,
                           cover_url:str|None=None,source_text:str='')->tuple[dict[str,Any],GatewayResponse]:
    labels={'wechat':'微信公众号','xhs':'小红书','poster':'活动招募海报','recap':'活动回顾'}
    cover_note=f"活动官方封面（已上传的主视觉，优先用作首图 / 海报主图）：{cover_url}\n" if cover_url else ''
    # 2026-10-07 修复：此前渠道生成只拿到「抽取后的 master / detail JSON」，原始方案全文根本没有进来
    # （木杆上下抽打、3.5 小时、《佛说造像度量经》、2023 年开业、1+1 航空座椅…这些最值钱的细节
    # 在 master 里只剩关键词），于是公众号推文只能把已经压缩过的事实再压缩一遍，又短又空。
    # 现在把第一手资料原文一并给模型，明确要求从中挖细节。
    raw_note=(f"\n原始方案全文（第一手资料，细节最全；下面这些具体数字 / 专名 / 动作就是最好的素材，"
              f"务必直接从里面挖，不要只靠上面的 JSON）：\n{source_text}\n" if source_text else '')
    briefs={
      'wechat':"""wechat（公众号图文）：这是**独立成篇的长文**，不是活动详情的删减版。写短了就是失败。
- 篇幅：正文 900~1400 字，7~9 个小节，每节 100~200 字。宁可 7 节写扎实，
  也不要 11 节每节只有两句——一堆短节凑在一起，读者只会觉得"什么都没说"。
- 结构：title（≤26 字，要有画面或判断，不要「XX 活动招募」）→ 开头 2~3 句钩子（lead）→
  若干 narrative 小节（headline 是有信息量的小标题、body 是 1~2 段连贯叙述，可按需带 mediaRefs）→
  一节「活动信息」（facts，写时间 / 交通 / 导游 / 费用包含）→ 收束（quote 或 cta）。
- 图片：mediaRefs 只引真实照片（kind='photo' 的 ref），贴在**对应叙述之后**，一节 0~2 张，不要全堆到文末。
- 禁止：把 detail 的 blocks 原样搬过来；「本次活动的亮点在于」式公文句；空话套话；
  编造资料里没有的日期 / 价格 / 名额 / 资质 / 用户评价。""",
      'xhs':"""xhs（小红书图文）：titleOptions[]（3 个，≤20 字，带钩子）、hook（首句抓人）、
body（真实口语，可换行，带 emoji 但要克制）、tags[]（6~10 个）、imageSequence[]（真实照片 ref 或 url）。
不要写成公文或硬广。""",
      'poster':"""poster（活动招募海报）：headline（≤14 字，一句就能立住）、subheadline、
facts[]（时间 / 地点 / 价格 / 人数，只写真实值）、sellingPoints[]（3~5 条，每条带具体细节）、
cta、preferredMediaRefs[]（真实照片 ref）。""",
      'recap':"""recap（活动回顾）：只有提供真实 actualActivityData / 现场素材时才能叙述实际发生事件；
资料不足时明确返回 needsActualData=true，绝不编造。""",
    }
    # 只把「编辑判断」交给渠道生成，**不传 detail 的 blocks**。
    # 实测：把整个 detail 成品塞进 prompt，模型会直接照抄它的标题与段落（qwen-max 尤其明显），
    # 结果公众号图文变成详情页的换皮版。渠道内容必须自己从原始资料重新写。
    detail_view={k:v for k,v in (detail or {}).items()
                 if k in ('activityUnderstanding','coreSellingIdea','editorialIntent') and v} \
                if isinstance(detail,dict) else {}
    prompt=f"""基于同一场活动，重新创作 {labels.get(channel,channel)} 原生内容。不是活动详情删减版。
{cover_note}
Activity Master（事实锚点）：{json.dumps(activity_master,ensure_ascii=False)}
详情页的编辑判断（只用来理解这场活动的定位，**禁止复用它的句子、小标题与结构**——本渠道是重新写一遍）：{json.dumps(detail_view,ensure_ascii=False)}
{raw_note}
{_WRITING_CONTRACT}

本渠道要求：
{briefs.get(channel,'')}
输出结构（强制；字段名不得改动）：
{{"title":"","summary":"","blocks":[{{"type":"lead|narrative|statement|media|gallery|facts|timeline|quote|cta","headline":"","body":"","mediaRefs":[],"items":[{{"label":"","value":""}}]}}]}}
正文**必须全部放进 blocks 数组**。不要自作主张换成 lead / narrative / facts / closing 这类顶层键——
前端只读 blocks，形状一变，整篇内容就会渲染成空白页。
严格 JSON，不要 Markdown，不要解释。"""
    gw=await generate_json(club_id=club_id,task_type=channel,system_prompt=SYSTEM,user_prompt=prompt)
    # 渠道成品（海报 / 小红书九宫格）同样只能用真实照片：品牌 logo 与空白底图不能上去
    photos={m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
            and m.get('ref') and (m.get('kind') or 'photo')=='photo'}
    if gw:
        data=gw.data or {}
        # 模型自创结构（lead/narrative/facts/closing）时把内容救回 blocks，别让前端渲染空白
        if channel in ('wechat','recap'): data=normalize_channel_blocks(data)
        if cover_url: data['coverUrl']=cover_url
        if isinstance(data.get('blocks'),list): data['blocks']=_sanitize_blocks(data['blocks'],allowed_refs=photos)
        # 公众号推文 / 海报同样不许出现成本数据：主视觉文案里印一行「人均 ¥3,806」同样是事故
        data,_changed=scrub_cost_data(data)
        # 与详情共用同一道事实防线：渠道文案一样会把品牌名音译掉（icebreaker → 冰破），
        # 以前这道检查只在详情上跑，推文里的错品牌名会直接发出去。
        if source_text:
            data=_map_strings(data,lambda t:restore_brand_names(t,source_text))
        return data,gw
    title=activity_master.get('title','活动');idea=detail.get('coreSellingIdea','')
    gallery=[m.get('ref') for m in activity_master.get('media',[]) if isinstance(m,dict)
             and m.get('ref') and (m.get('kind') or 'photo')=='photo']
    if channel=='wechat':data={'title':title,'summary':idea,'coverUrl':cover_url,'blocks':detail.get('blocks',[])[:6]+[{'type':'cta','headline':'查看活动详情并报名'}]}
    elif channel=='xhs':data={'titleOptions':[title,f"周末去{activity_master.get('location','山里')}，这次不赶行程"],'hook':idea,'body':idea+'\n\n具体日期、费用和报名信息见活动详情。','tags':['户外','周末去哪儿','自然'],'imageSequence':([cover_url] if cover_url else [])+gallery[:8],'coverUrl':cover_url}
    elif channel=='poster':data={'headline':title,'subheadline':idea,'facts':[activity_master.get('date',''),activity_master.get('location','')],'sellingPoints':[idea],'cta':'扫码查看详情与报名','preferredMediaRefs':([cover_url] if cover_url else [])+gallery[:1],'coverUrl':cover_url}
    else:data={'needsActualData':True,'title':f'{title}｜活动回顾','message':'请上传现场照片或领队记录后再生成真实活动回顾。'}
    return data,record_mock_usage(club_id,channel,prompt,data)
