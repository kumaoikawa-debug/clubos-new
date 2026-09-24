from __future__ import annotations
import mimetypes, re, shutil, zipfile
from pathlib import Path
from typing import Iterable
import fitz
from pptx import Presentation
from docx import Document
from PIL import Image

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
                        parts.append(' | '.join(c.text.strip() for c in rr.cells))
            out.append(f"[Slide {i}]\n"+'\n'.join(parts))
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


def image_meta(path: Path, public_url: str | None = None, source: str | None = None, page: int | None = None):
    try:
        with Image.open(path) as im:
            return {
                'path':str(path),'name':path.name,'url':public_url or '',
                'width':im.width,'height':im.height,
                'orientation':'landscape' if im.width>im.height else 'portrait' if im.height>im.width else 'square',
                'source':source or path.name,'page':page,
            }
    except Exception:
        return None


def _extract_office_media(path: Path, out_dir: Path) -> list[Path]:
    """Extract embedded PPTX/DOCX media without re-rendering the document.
    The model still receives the full original text and the original embedded visual assets.
    """
    media=[]
    prefix='ppt/media/' if path.suffix.lower()=='.pptx' else 'word/media/'
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.startswith(prefix): continue
                ext=Path(name).suffix.lower()
                if ext not in IMAGE_EXTS: continue
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
    texts=[]; images=[]; files=[]
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
        if t.strip(): texts.append(f"[文件: {p.name}]\n{t}")
        extracted=[]
        if p.suffix.lower() in {'.pptx','.docx'}:
            extracted=_extract_office_media(p,batch_dir/'extracted')
        elif p.suffix.lower()=='.pdf':
            extracted=_extract_pdf_previews(p,batch_dir/'extracted')
        for idx,img in enumerate(extracted,1):
            rel=''
            if static_root:
                try: rel='/static/'+str(img.relative_to(static_root)).replace('\\','/')
                except Exception: pass
            m=image_meta(img,rel,source=p.name,page=idx)
            if m: images.append(m)
    # Give every image a stable ref the model can cite.
    for i,img in enumerate(images,1): img['ref']=f'img_{i:02d}'
    return {
        'text':normalize_text('\n\n'.join(texts)),
        'images':images,
        'files':files,
        'media_manifest':[{'ref':x['ref'],'name':x['name'],'url':x.get('url',''),'width':x['width'],'height':x['height'],'source':x.get('source'),'page':x.get('page')} for x in images]
    }
