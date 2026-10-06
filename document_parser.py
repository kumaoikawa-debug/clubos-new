from __future__ import annotations
import math, mimetypes, re, shutil, zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable
import fitz
from pptx import Presentation
from docx import Document
from PIL import Image

from cost_guard import redact_cost_text

ALLOWED = {'.pptx','.docx','.pdf','.txt','.md','.jpg','.jpeg','.png','.webp'}
IMAGE_EXTS = {'.jpg','.jpeg','.png','.webp'}


def _safe_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._\-\u4e00-\u9fff]+','_',Path(name).name)


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {'.txt','.md'}:
        return path.read_text(encoding='utf-8', errors='ignore')
    if ext == '.pptx':
        prs = Presentation(str(path)); out=[]
        for i,slide in enumerate(prs.slides,1):
            parts=[]
            for shape in slide.shapes:
                txt=getattr(shape,'text','') or ''
                if txt.strip(): parts.append(txt.strip())
                if getattr(shape,'has_table',False):
                    for rr in shape.table.rows:
                        row=' | '.join(c.text.strip() for c in rr.cells)
                        if row.strip(): parts.append(row)
            # 只在确实有文字时才落 [Slide N] 标记：纯图片页（设计稿式方案）不该产生
            # 任何"看起来像内容"的字符，否则会让「上传了文档却读不到字」的 422 拦截被绕过，
            # 也会把空标记当成噪声喂给 AI。
            if parts: out.append(f"[Slide {i}]\n"+'\n'.join(parts))
        return '\n\n'.join(out)
    if ext == '.docx':
        d=Document(str(path)); out=[p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for r in t.rows: out.append(' | '.join(c.text.strip() for c in r.cells))
        return '\n'.join(out)
    if ext == '.pdf':
        doc=fitz.open(str(path)); return '\n\n'.join(f"[Page {i+1}]\n{p.get_text()}" for i,p in enumerate(doc))
    return ''


def normalize_text(text: str) -> str:
    # Preserve source context. Never silently truncate because of plan, credits or cost.
    return re.sub(r'\n{3,}', '\n\n', text).strip()


# ---------------------------------------------------------------------------
# 图片分类：把「真实照片」和「品牌 logo / 字标 / 空白底图 / 截图 / 扁平海报」分开
#
# 上传的方案里，品牌 logo（如 icebreaker 标志）、字标横幅（icebreaker / Move to natural）、
# 空白幻灯片底图、地图与天气截图、赞助商海报，都会作为嵌入图片被一起抽出来。以前它们与
# 真实照片混在同一个清单里按顺序取用，于是头图变成了品牌 logo、图集里全是纯黑图。
# 现在在解析期就给每张图打标，AI 只允许引用 kind='photo' 的图片。
#
# 三个判据都是尺度无关的（不依赖分辨率与文件大小 —— 高像素照片的「字节/像素」反而更低，
# 用体积判断会把 3000px 的手机照误判成扁平图）：
#   * 短边 < 300        → logo / 图标 / 缩略图
#   * 主色占比 > 0.5    → 单色底 + 少量线条：字标、空白页、色块海报
#   * 色彩熵 < 2.5 bit  → 用色过少（图标网格、线稿、纯文字页），不可能是照片
# 实测（一份 26 张图的真实方案）：真实照片短边 ≥ 366 / 主色占比 ≤ 0.42 / 熵 ≥ 3.59；
# 品牌 logo 与字标短边 59~277 / 主色占比 0.77~1.0 / 熵 -0.0~1.41；地图·天气截图
# 主色占比 0.59~0.82。三类阈值都有余量。
# ---------------------------------------------------------------------------
def _image_kind(im: Image.Image) -> str:
    """'photo' = 可当活动照片用；'logo' = 品牌标 / 扁平图 / 空白底 / 截图，不进 C 端。

    判不出来时一律返回 'photo'：漏掉一张 logo，好过整份资料一张图都不剩。"""
    try:
        w, h = im.size
        if min(w, h) < 300:
            return 'logo'
        raw = im.convert('RGB').resize((64, 64)).tobytes()
        buckets: Counter = Counter()
        for i in range(0, len(raw), 3):
            buckets[(raw[i] >> 5, raw[i + 1] >> 5, raw[i + 2] >> 5)] += 1
        total = sum(buckets.values()) or 1
        if buckets.most_common(1)[0][1] / total > 0.5:
            return 'logo'
        entropy = -sum((v / total) * math.log2(v / total) for v in buckets.values())
        if entropy < 2.5:
            return 'logo'
        return 'photo'
    except Exception:
        return 'photo'


def image_meta(path: Path, public_url: str | None = None, source: str | None = None, page: int | None = None,
               classify: bool = True):
    try:
        with Image.open(path) as im:
            return {
                'path':str(path),'name':path.name,'url':public_url or '',
                'width':im.width,'height':im.height,
                'orientation':'landscape' if im.width>im.height else 'portrait' if im.height>im.width else 'square',
                'source':source or path.name,'page':page,
                # classify=False 用于 PDF 渲染页：整页就是用户上传的内容本身，
                # 不是「混在资料里的零散配图」，不该被当成 logo 过滤掉。
                'kind': _image_kind(im) if classify else 'photo',
            }
    except Exception:
        return None


def _natural_key(name: str):
    """按数字自然序排序（image2 排在 image10 前面），保证幻灯片里的图片顺序不乱。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', name)]


def _extract_office_media(path: Path, out_dir: Path) -> list[Path]:
    """Extract embedded PPTX/DOCX media without re-rendering the document.
    The model still receives the full original text and the original embedded visual assets.

    不同工具导出的 PPTX 包结构并不统一：Office 默认放在 `ppt/media/`，
    而 Keynote / 在线设计工具「另存为 pptx」会放在 `ppt/slides/media/`。
    以前这里写死 `ppt/media/`，设计稿式方案（每页一张大图 + 少量文字）会一张图都提不出来——
    直接把这种资料交给模型，等于只给了它几行文字，成品自然「不按资料来」。
    所以这里不再依赖固定前缀：先取任意 `/media/` 目录下的图片，取不到再兜底扫全包图片。
    """
    media=[]
    try:
        with zipfile.ZipFile(path) as z:
            entries=[n for n in z.namelist() if not n.endswith('/') and not n.startswith('docProps/')]
            def is_image(n: str) -> bool:
                return Path(n).suffix.lower() in IMAGE_EXTS
            # 优先命中「媒体目录」（ppt/media/、ppt/slides/media/、word/media/ ...），
            # 避免把图标、缩略图之类的零散图片当成幻灯片正文图。
            names=[n for n in entries if is_image(n) and '/media/' in n]
            if not names:
                names=[n for n in entries if is_image(n)]
            for name in sorted(names,key=_natural_key):
                dest=out_dir/f"{path.stem}_{Path(name).name}"
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(z.read(name))
                media.append(dest)
    except Exception:
        pass
    return media


def _extract_pdf_previews(path: Path, out_dir: Path, max_pages: int = 24) -> list[Path]:
    previews=[]
    try:
        doc=fitz.open(str(path))
        for i,page in enumerate(doc):
            if i>=max_pages: break
            pix=page.get_pixmap(matrix=fitz.Matrix(1.35,1.35),alpha=False)
            dest=out_dir/f"{path.stem}_page_{i+1:02d}.jpg"
            pix.save(str(dest)); previews.append(dest)
    except Exception:
        pass
    return previews


def save_uploads(files, upload_dir: Path, *, max_total_bytes: int | None = None):
    # Never silently truncate source documents. Reject oversize requests before calling AI.
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved=[]; total=0
    if max_total_bytes is not None and len(files)>20:
        raise ValueError('at most 20 source files per generation')
    for f in files:
        ext=Path(f.filename or '').suffix.lower()
        if ext not in ALLOWED:
            if max_total_bytes is not None:raise ValueError(f'unsupported file type: {ext}')
            continue
        dest=upload_dir/_safe_name(f.filename or f'upload{ext}')
        if dest.exists():
            dest=upload_dir/(dest.stem+'_'+str(len(saved))+dest.suffix)
        if max_total_bytes is None:
            dest.write_bytes(f.file.read())
        else:
            with dest.open('wb') as output:
                while True:
                    chunk=f.file.read(min(1024*1024,max_total_bytes-total+1))
                    if not chunk:break
                    total+=len(chunk)
                    if total>max_total_bytes:
                        output.close();dest.unlink(missing_ok=True)
                        raise ValueError('source files exceed 20MB total limit')
                    output.write(chunk)
        saved.append(dest)
    return saved


def parse_sources(paths: Iterable[Path], user_text: str='', static_root: Path | None=None):
    texts=[]; images=[]; files=[]; cost_stats={'costLines':0,'costBlocks':0}; raw_len=0
    if user_text.strip(): texts.append('[用户输入]\n'+user_text.strip())
    for p in paths:
        files.append({'name':p.name,'ext':p.suffix.lower()})
        batch_dir=p.parent
        if p.suffix.lower() in IMAGE_EXTS:
            rel=''
            if static_root:
                try: rel='/static/'+str(p.relative_to(static_root)).replace('\\','/')
                except Exception: pass
            m=image_meta(p,rel,source=p.name)
            if m: images.append(m)
            continue
        t=extract_text(p)
        # ★ 成本闸门（2026-10-06）：喂给 AI 的文本里就不许出现成本/报价数据。
        # 一份始祖鸟高客方案的第 15 页是「14 — COST 活动费用明细」（单价/小计/合计（未含税）/
        # 人均费用/策划执行 10%），此前会被原样喂给模型、再被抓成 activity_master.fees
        # 渲染到 C 端「费用说明 PRICE」卡片上 —— 等于把俱乐部的成本底价摊给顾客看。
        # 这里在源头剔掉成本行，只保留白名单里的服务项名（含门票/含氧气/含摄影）。
        raw_len+=len(t)
        t,cost_st=redact_cost_text(t)
        cost_stats['costLines']+=cost_st['costLines']; cost_stats['costBlocks']+=cost_st['costBlocks']
        if t.strip(): texts.append(f"[文件: {p.name}]\n{t}")
        extracted=[]
        is_page_render=p.suffix.lower()=='.pdf'
        if p.suffix.lower() in {'.pptx','.docx'}:
            extracted=_extract_office_media(p,batch_dir/'extracted')
        elif p.suffix.lower()=='.pdf':
            extracted=_extract_pdf_previews(p,batch_dir/'extracted')
        for idx,img in enumerate(extracted,1):
            rel=''
            if static_root:
                try: rel='/static/'+str(img.relative_to(static_root)).replace('\\','/')
                except Exception: pass
            m=image_meta(img,rel,source=p.name,page=idx,classify=not is_page_render)
            if m: images.append(m)
    # Give every image a stable ref the model can cite.
    for i,img in enumerate(images,1): img['ref']=f'img_{i:02d}'
    return {
        'text':normalize_text('\n\n'.join(texts)),
        'images':images,
        'files':files,
        # 成本清洗台账：上线后用它判断「这份方案里的成本有没有被挡住」。
        # textLengthRaw 是**清洗前**的字数 —— 上传了文档却一个字都读不出来时仍要当场 422，
        # 这个判定必须按原文长度来，不能被清洗结果误伤（一份纯成本页的方案不该被当成空文档）。
        'costRedacted':cost_stats,
        'textLengthRaw':raw_len,
        'media_manifest':[{'ref':x['ref'],'name':x['name'],'url':x.get('url',''),'width':x['width'],'height':x['height'],'source':x.get('source'),'page':x.get('page'),'kind':x.get('kind','photo')} for x in images]
    }
