"""把「一条网页链接」变成 AI 能读的活动资料（2026-10-28 用户要求）。

老板的真实动作是：别家公众号写好的活动推文 / 自家官网的活动页 / 小红书笔记，
直接甩一条链接过来，希望 AI 照着做一份详情。此前只认上传的 PPT/Word/PDF/图片，
链接只能靠人肉复制粘贴。

这里只做一件事：**把链接抓成 text + 本地图片**，后面的解析、成本清洗、图片打标、
排版全部复用 document_parser / ai_engine 的既有链路 —— 不新开一条生成路径，
两条入口（上传 / 链接）最终喂给模型的 source 结构完全一致。

三条红线：
1. **SSRF**：链接是用户给的任意字符串，服务端去 fetch 等于给它开内网通道。
   所以每一跳（含 302）都校验：只允许 http(s)、域名必须解析到公网 IP、
   拒绝 localhost / 内网段 / link-local（云元数据 169.254.169.254 正在此列）。
2. **不能拖垮请求**：页面 6MB 上限、图片 10 张 / 单张 5MB、整段 30 秒硬截止。
   live 模式下这段是同步跑的（要立刻回 jobId），慢一点就会吃网关超时。
3. **不许静默成功**：抓不到、被反爬挡了、正文为空，一律抛 LinkError 带人话原因，
   由接口层变成 422 告诉老板「这条链接读不到，换一条或直接上传」，
   而不是产出一份全是占位文案的详情还扣 Credits。
"""
from __future__ import annotations

import html as _html
import io
import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

import httpx
from PIL import Image

MAX_HTML_BYTES = 6 * 1024 * 1024
# 图片下载上限（2026-10-29 提升 10→24）：户外俱乐部推文普遍是「整段设计图 + 大量实拍」的画报式排版，
# 旧上限 10 张会把一半配图丢掉，导致详情页排出来「整版图」断料、画报感不足。
# 成本权衡：下发给模型的只是 ref 名（link_01…），**不是图片本身**，所以更多图片 = 更多可引用素材、
# 几乎不增加模型 token；增加的只有磁盘落盘与下载耗时（见 TOTAL_DEADLINE 放宽）。
# 真正约束下载张数的是下方 4 线程池 + TOTAL_DEADLINE 双重闸，本值只是「最多尝试多少张」的上界。
MAX_IMAGES = 24
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
# 硬截止随图片上限同步放宽（30→45s）：4 线程并行、单图 8s 超时，24 张最多 6 批 ≈ 48s 最坏，
# 留 45s 让「整段设计图 + 关键实拍」基本都能落盘；live 同步链路网关掐 >60s，45s 仍安全。
TOTAL_DEADLINE = 45.0          # 秒：整条链接（页面 + 配图）的硬截止
LINK_TEXT_MAX = 18000          # 字：超出截断，控制 token 成本（公众号长文普遍 8k~15k）
MIN_IMG_SIDE = 320             # 小于此边长的当图标丢掉
MIN_IMG_AREA = 160_000

# 微信 / 头条这类平台没 UA 只返回「请在微信客户端打开」或 302 到验证页，
# 带一个移动端微信 UA 才能拿到真正的正文（2026-10-28 实测：公众号文章 200 + 4MB 正文）。
_UA_WECHAT = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44(0x18002c2f) '
              'NetType/WIFI Language/zh_CN')
_UA_BROWSER = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

_BLOCK_TAGS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'tr', 'td', 'th', 'section', 'article', 'blockquote', 'pre', 'figure',
               'figcaption', 'hr', 'table', 'dd', 'dt'}
_SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'iframe', 'video', 'audio', 'head'}


class LinkError(Exception):
    """链接读不出来。message 是给人看的中文原因，直接进 422。"""


# ── 链接规范化 ───────────────────────────────────────────────────────────────
def normalize_url(raw: str) -> str:
    """用户在输入框里贴的往往是「mp.weixin.qq.com/s/xxx」这种没有协议头的串。"""
    s = str(raw or '').strip()
    s = s.replace('\u200b', '').replace('\n', '').replace(' ', '')
    if not s:
        raise LinkError('链接是空的')
    # 微信「复制链接」有时带 &amp; 之类的 HTML 转义
    s = s.replace('&amp;', '&')
    if not re.match(r'^https?://', s, re.I):
        if re.match(r'^//', s):
            s = 'https:' + s
        elif re.match(r'^[\w.-]+\.[a-z]{2,}(/|$)', s, re.I):
            s = 'https://' + s
        else:
            raise LinkError('这不像一个网页链接，请贴以 http(s):// 开头的完整地址')
    if len(s) > 2000:
        raise LinkError('链接太长了（超过 2000 字符），可能没复制完整')
    return s


def _assert_public(url: str) -> None:
    """SSRF 闸门：每一跳都要过。"""
    p = urlsplit(url)
    if p.scheme not in ('http', 'https'):
        raise LinkError('只支持 http / https 链接')
    host = (p.hostname or '').strip().lower()
    if not host:
        raise LinkError('链接里没有域名')
    if host == 'localhost' or host.endswith('.local') or host.endswith('.internal'):
        raise LinkError('不能读取内网地址')
    # 直接把 IP 写进链接的：先按 IP 判
    try:
        ip = ipaddress.ip_address(host)
        if not _ip_public(ip):
            raise LinkError('不能读取内网地址')
        return
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise LinkError('这个域名解析不了，请检查链接是否完整、有没有写错')
    if not infos:
        raise LinkError('这个域名解析不了')
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _ip_public(ip):
            raise LinkError('不能读取内网地址')


def _ip_public(ip) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


# ── 抓页面（手动跟随 302，逐跳校验） ────────────────────────────────────────
def _fetch_html(url: str, deadline: float, ua: str | None = None) -> tuple[str, str]:
    """返回 (最终 URL, HTML 文本)。

    微信文章：移动端 MicroMessenger UA 会被微信反爬验证码拦截（落到 wappoc_appmsgcaptcha、
    无 #js_content 正文容器）。调用方（fetch_link）对微信链接优先桌面浏览器 UA、
    必要时回退移动端 UA；本函数只负责拿 HTML，正文容器判定交给 _Extractor。
    """
    if ua is None:
        ua = _UA_WECHAT if 'mp.weixin.qq.com' in url else _UA_BROWSER
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.6',
    }
    cur = url
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=False) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            _assert_public(cur)
            if time.monotonic() > deadline:
                raise LinkError('这条链接打开太慢（超过 30 秒），请稍后重试或直接上传资料')
            try:
                with client.stream('GET', cur, headers=headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get('location') or ''
                        if not loc:
                            raise LinkError(f'链接跳转异常（HTTP {resp.status_code}）')
                        cur = urljoin(cur, loc)
                        continue
                    if resp.status_code == 404:
                        raise LinkError('这个链接打不开（404），可能已被作者删除')
                    if resp.status_code in (401, 403):
                        raise LinkError('这个链接需要登录或被站点拒绝访问，请换公开链接或直接上传资料')
                    if resp.status_code >= 400:
                        raise LinkError(f'这个链接打不开（HTTP {resp.status_code}）')
                    ctype = (resp.headers.get('content-type') or '').lower()
                    if ctype and 'html' not in ctype and 'text' not in ctype:
                        raise LinkError(f'这个地址不是网页（{ctype.split(";")[0]}），'
                                        '如果要传 PDF 请用下面的「上传资料」')
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_HTML_BYTES:
                            raise LinkError('这个页面太大了（超过 6MB），没法整篇读取')
                    charset = 'utf-8'
                    m = re.search(r'charset=([\w-]+)', ctype)
                    if m:
                        charset = m.group(1)
                    try:
                        return cur, bytes(buf).decode(charset, 'ignore')
                    except LookupError:
                        return cur, bytes(buf).decode('utf-8', 'ignore')
            except LinkError:
                raise
            except httpx.TimeoutException:
                raise LinkError('打开这条链接超时了，请稍后重试或直接上传资料')
            except httpx.HTTPError as e:
                raise LinkError(f'读取这条链接失败（{e.__class__.__name__}），请检查网络或换一条')
    raise LinkError('链接跳转次数太多，可能是个短链陷阱，请用最终页面的地址')


# ── 抓页面（headless 浏览器渲染 JS 后取完整 DOM） ──────────────────────────
def _chrome_bin() -> str | None:
    """找一个可用的 Chrome/Chromium 可执行文件。

    优先环境变量 CLUBOS_CHROME_BIN，其次常见安装路径（macOS / Linux / Windows）。
    找不到返回 None —— 此时 _fetch_rendered 自动回落静态路径。
    """
    import os
    import shutil
    env = os.environ.get('CLUBOS_CHROME_BIN')
    if env and os.path.exists(env):
        return env
    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium', '/usr/bin/chromium-browser',
        shutil.which('google-chrome') or '',
        shutil.which('chromium') or '',
        shutil.which('chromium-browser') or '',
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _fetch_rendered(url: str, ua: str, deadline: float) -> tuple[str, str]:
    """用 headless 浏览器渲染 JS 后返回 (最终 URL, 完整 HTML)。

    这是静态抓取的兜底：很多活动页是 SPA / JS 动态渲染，静态 HTML 只有空壳
    （秀米之外的建站平台、React/Vue 活动页、靠接口注水的内容）。交给浏览器跑一遍
    JS 再抽，能拿到真正的正文与配图——这正是「全内容抓取」的关键一环。

    优先用系统已装的 Chrome/Chromium（--dump-dom，零额外依赖）；没有则尝试 playwright；
    都没有 → 返回 ('', '')，调用方回落静态路径，绝不因缺依赖而报错（线上零回归）。
    """
    binpath = _chrome_bin()
    if binpath:
        import os
        import shutil
        import subprocess
        import tempfile
        udir = tempfile.mkdtemp(prefix='clubos_render_')
        try:
            remain = max(10.0, deadline - time.monotonic())
            vt = min(10000, int(remain * 1000))
            proc = subprocess.run(
                [binpath, '--headless', '--no-sandbox', '--disable-gpu',
                 '--disable-dev-shm-usage', '--disable-extensions',
                 '--no-first-run', '--no-default-browser-check',
                 f'--user-data-dir={udir}', f'--virtual-time-budget={vt}',
                 '--run-all-compositor-stages-before-draw',
                 f'--user-agent={ua}', '--dump-dom', url],
                capture_output=True,
                timeout=int(remain) + 8)
            html = proc.stdout.decode('utf-8', 'ignore')
            if not html.strip():
                return '', ''
            return url, html
        except Exception:
            return '', ''
        finally:
            shutil.rmtree(udir, ignore_errors=True)
    # 退化到 playwright（部署环境若装了 chromium）
    try:
        from playwright.sync_api import sync_playwright
        remain = max(8.0, deadline - time.monotonic())
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            page = browser.new_page(user_agent=ua)
            try:
                page.goto(url, wait_until='domcontentloaded',
                          timeout=int(remain * 1000))
            except Exception:
                pass
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
            html = page.content() or ''
            final_url = page.url or url
            try:
                browser.close()
            except Exception:
                pass
            return final_url, html
    except Exception:
        return '', ''


# ── HTML → 正文 + 配图 ──────────────────────────────────────────────────────
class _Extractor(HTMLParser):
    """从 HTML 里挑出「正文容器」，收文本和小图。

    优先 微信 #js_content → <article> → <main> → <body>，取第一个有实质内容的。
    这样公众号 / 新闻站 / 普通活动页都能落到正文区，不会把导航、页脚、评论一起喂给模型。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.buckets: list[dict] = []     # 活跃容器
        self.found: list[dict] = []       # 见过的候选容器（关掉后仍在）
        self._skip = 0
        self.title = ''
        self._in_title = False
        self.og_title = ''
        self.og_image = ''
        self.meta_desc = ''
        self.is_wechat = False   # 命中 #js_content 容器即视为公众号文章（即便正文烤在图里）

    # --- 容器管理 ---
    def _open(self, kind: str):
        b = {'kind': kind, 'depth': self.depth, 'text': [], 'imgs': []}
        self.buckets.append(b)
        self.found.append(b)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            prop = (a.get('property') or a.get('name') or '').lower()
            content = (a.get('content') or '').strip()
            if prop in ('og:title', 'twitter:title') and content and not self.og_title:
                self.og_title = content
            elif prop in ('og:image', 'twitter:image') and content and not self.og_image:
                self.og_image = content
            elif prop == 'description' and content and not self.meta_desc:
                self.meta_desc = content
            return
        if tag == 'title':
            self._in_title = True
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        self.depth += 1
        if self._skip:
            return
        if tag == 'img':
            src = a.get('data-src') or a.get('data-original') or a.get('src') or ''
            src = src.strip()
            if src and not src.startswith('data:'):
                for b in self.buckets:
                    b['imgs'].append(src)
            return
        cid = (a.get('id') or '').lower()
        if cid == 'js_content':
            self._open('wechat')
            self.is_wechat = True
        elif tag == 'article':
            self._open('article')
        elif tag == 'main':
            self._open('main')
        elif tag == 'body':
            self._open('body')
        if tag in _BLOCK_TAGS:
            for b in self.buckets:
                b['text'].append('\n')

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
            return
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if not self._skip:
            if tag in _BLOCK_TAGS:
                for b in self.buckets:
                    b['text'].append('\n')
            self.buckets = [b for b in self.buckets if b['depth'] < self.depth]
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._skip:
            return
        if not data or not data.strip():
            # 纯空白也要留着，否则中英文之间的空格会丢
            if data:
                for b in self.buckets:
                    b['text'].append(' ')
            return
        for b in self.buckets:
            b['text'].append(data)

    # --- 结果 ---
    def best(self) -> tuple[str, str, list[str]]:
        """返回 (容器类型, 正文, 图片)。容器类型用来判「这是不是真文章」。

        微信公众号常有「图片型长图」推文：#js_content 里是若干 <section> 各塞一张图、
        正文文字烤在图里（静态 HTML 几乎无文字）。这类只要 #js_content 容器 + 有图，
        就算有效文章，交给 AI 走画报式排版，不应因「无文字」被拒；只有既无文字又无图
        的微信页（如失效/验证页）才落到后面的报错分支。
        """
        for kind in ('wechat', 'article', 'main', 'body'):
            for b in self.found:
                if b['kind'] != kind:
                    continue
                text = _clean_text(''.join(b['text']))
                if kind == 'wechat':
                    # 微信：有正文，或（图片型长图）无正文但有图 → 都算文章
                    if len(text) >= 40 or b['imgs']:
                        return kind, text, b['imgs']
                elif len(text) >= 40:
                    return kind, text, b['imgs']
        return '', '', []
    def page_title(self) -> str:
        t = (self.og_title or self.title or '').strip()
        t = re.sub(r'\s+', ' ', t)
        # 公众号标题常带「- 微信公众平台」这类后缀
        t = re.sub(r'\s*[-|—]\s*(微信公众平台|微信公众号|微信公众号文章)\s*$', '', t)
        return t[:120]


def _clean_text(s: str) -> str:
    lines = []
    for raw in s.replace('\u200b', '').replace('\u00a0', ' ').split('\n'):
        line = re.sub(r'[ \t\r\f\v]+', ' ', raw).strip()
        if not line:
            continue
        # 反爬/版权噪声行：留在正文里只会污染模型
        if line in {'预览时标签不可点', '微信扫一扫', '微信扫一扫关注该公众号',
                    '继续滑动看下一个', '向上滑动看下一个', '轻触阅读原文',
                    '知道了', '取消', '允许', '视频', '小程序'}:
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return '\n'.join(lines)


def _img_urls_ok(u: str) -> bool:
    low = u.lower().split('?')[0]
    if low.endswith(('.gif', '.svg', '.ico', '.webp')):
        return False
    return True


def _download_images(urls: list[str], page_url: str, dest_dir: Path, deadline: float) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    picked: list[str] = []
    for u in urls:
        if len(picked) >= MAX_IMAGES:
            break
        full = urljoin(page_url, u)
        if not full.lower().startswith(('http://', 'https://')):
            continue
        if not _img_urls_ok(full) or full in picked:
            continue
        picked.append(full)
    if not picked:
        return []

    headers = {'User-Agent': _UA_WECHAT if 'qpic.cn' in picked[0] else _UA_BROWSER,
               'Referer': page_url}

    def one(item):
        idx, u = item
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True) as c:
                r = c.get(u, headers=headers)
            if r.status_code != 200:
                return None
            data = r.content
            if not data or len(data) > MAX_IMAGE_BYTES:
                return None
            im = Image.open(io.BytesIO(data))
            im.load()
            w, h = im.size
            if w < MIN_IMG_SIDE or h < MIN_IMG_SIDE or w * h < MIN_IMG_AREA:
                return None
            # 极扁/极长的多半是分割线、装饰条、截图的某个边
            if w / max(h, 1) > 4 or h / max(w, 1) > 4:
                return None
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            if w > 1600:
                im = im.resize((1600, max(1, round(h * 1600 / w))), Image.LANCZOS)
            out = dest_dir / f'link_{idx:02d}.jpg'
            im.save(out, 'JPEG', quality=88, optimize=True)
            return out
        except Exception:
            return None

    saved: list[Path] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for res in pool.map(one, list(enumerate(picked, 1))):
            if time.monotonic() > deadline:
                break
            if res is not None:
                saved.append(res)
    return saved


# ── 秀米（Xiumius）board：正文是 JS 渲染的，静态 HTML 只是壳 ───────────────
# 真实的整篇排版在 injectedData.showInfo.show_data_url 指向的那份 JSON 里，
# 字段 cubes[0].pages[0].layers[0]._comp["_$raHTML"] 是一整段「已渲染」的 HTML
# （含全部 <p> 正文与 <img> 配图）。这份 JSON 是 gzip 的，httpx 会自动解压。
# 所以秀米不需要上浏览器，纯 httpx 就能拿全文 + 配图，部署零新增依赖。
_XIUMI_HOSTS = ('xiumius.cn',)


def _is_xiumius(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ''
    except Exception:
        return False
    return host.endswith(_XIUMI_HOSTS) and '/board/' in url


def _xiumius_data_url(html: str) -> str:
    """从 board 静态页的 __pre_data 里抽出内容 JSON 地址。"""
    m = re.search(r'showInfo\s*=\s*JSON\.parse\(decodeURIComponent\("([^"]*)"\)', html)
    if not m:
        raise LinkError('这个秀米链接读不到内容数据，请确认链接是「打开后能看到排版的 board 页面」')
    try:
        info = json.loads(unquote(m.group(1)))
    except Exception:
        raise LinkError('这个秀米链接的内容数据解析失败，请换一条或上传资料')
    du = (info.get('show_data_url') or '').strip()
    if not du:
        raise LinkError('这个秀米链接没有关联内容数据')
    if du.startswith('//'):
        du = 'https:' + du
    elif du.startswith('/'):
        du = 'https://xiumius.cn' + du
    return du


def _html_fragment_text_imgs(html: str) -> tuple[str, list[str]]:
    """从一段 HTML 片段里抽正文文本、图片地址，并**保留原排版的章节骨架与图片图注**。

    2026-10-29 重写：旧实现把整段 HTML 标签全剥成纯文本，章节标题、图片图注全部丢失 ——
    喂给写作模型的就是一坨扁平文字，于是模型既抓不到原文的分节结构（只能自己套模板），
    也丢掉了原产品里的图片说明。重写后：
      · 以 <section> 为逻辑块边界（秀米每个内容块都是一个 section），块之间留空行；
      · <h1/h2/h3> 识别为小节标题，标成「## 标题」还原原排版的分节顺序；
      · 图片原位保留，把它的 alt 或紧随其后的短文字当作图注，写成「（图：…）」；
      · 正文段落照常保留。
    结构线索只给写作模型当骨架参考，事实仍由 _fact_digest 从正文抽取，不会污染事实提取。"""
    html = re.sub(r'<script[\s\S]*?</script>', ' ', html, flags=re.I)
    html = re.sub(r'<style[\s\S]*?</style>', ' ', html, flags=re.I)

    class _X(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks = []          # 每个元素 {'kind','text':[],'img':None,'alt':'','cap':''}
            self.cur = None
            self.skip = 0

        def _blk(self, kind):
            self.cur = {'kind': kind, 'text': [], 'img': None, 'alt': '', 'cap': ''}
            self.blocks.append(self.cur)

        def _push(self, s):
            if not s:
                return
            # 图片块不接收文字：图注在下一节单独成块，不要混进图片块
            if self.cur is None:
                self._blk('text')
            elif self.cur['kind'] == 'image':
                return
            self.cur['text'].append(s)

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag in ('script', 'style'):
                self.skip += 1
                return
            if self.skip:
                return
            if tag == 'section':
                self.cur = None               # 块边界：收尾当前块
                return
            if tag in ('h1', 'h2', 'h3'):
                self._blk('heading')
                return
            if tag == 'img':
                src = d.get('src') or d.get('data-src') or d.get('data-original') or ''
                alt = (d.get('alt') or '').strip()
                if src and not src.startswith('data:'):
                    self._blk('image')
                    self.cur['img'] = src
                    if alt:
                        self.cur['alt'] = alt
                return
            if tag == 'br':
                self._push('\n')
                return
            if tag in ('p', 'div', 'li', 'tr', 'td'):
                if self.cur and self.cur['kind'] == 'image':
                    self.cur = None

        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                if self.skip:
                    self.skip -= 1
                return
            if self.skip:
                return
            if tag in ('h1', 'h2', 'h3'):
                self.cur = None
                return
            if tag == 'section':
                self.cur = None
                return
            if tag in ('p', 'div', 'li', 'tr', 'td'):
                self._push('\n')

        def handle_data(self, data):
            if self.skip:
                return
            if not data.strip():
                return
            self._push(data)

    p = _X()
    try:
        p.feed(html)
    except Exception:
        pass
    p.close()

    # 收尾：把文字块拼好、清洗；图片块保持
    cleaned = []
    for b in p.blocks:
        if b['kind'] == 'image':
            cleaned.append(b)
            continue
        t = _clean_text(''.join(b['text']))
        if t:
            b['text'] = [t]
            cleaned.append(b)

    # 图注关联：图片块后紧跟一个「很短、无图」的文字块，视为它的图注
    for i, b in enumerate(cleaned):
        if b['kind'] != 'image':
            continue
        nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
        if nxt and nxt['kind'] == 'text':
            t = nxt['text'][0]
            # 图注通常很短（≤ 24 字）且不像一段正文（不含长句标点）
            if len(t) <= 24 and not any(c in t for c in '。；;！!？?'):
                b['cap'] = t
                nxt['_consumed'] = True
        if not b.get('cap') and b.get('alt'):
            b['cap'] = b['alt']

    # 组装文本：标题 → ## 标题；图片 → （图：图注）；正文照常
    out = []
    for b in cleaned:
        if b.get('_consumed'):
            continue
        if b['kind'] == 'heading':
            out.append('## ' + b['text'][0])
        elif b['kind'] == 'image':
            cap = b.get('cap') or ''
            out.append('（图' + ('：' + cap if cap else '') + '）')
        else:
            out.append(b['text'][0])
    text = '\n\n'.join(out)

    # 图片去重（顺序保留，供照片挑选按序对齐）
    imgs = []
    seen = set()
    for b in p.blocks:
        if b['kind'] == 'image' and b['img'] and b['img'] not in seen:
            seen.add(b['img'])
            imgs.append(b['img'])
    return text, imgs


def _fetch_xiumius(url: str, dest_dir: Path, deadline: float) -> dict:
    _assert_public(url)
    # 1) 抓 board 静态页（含 injectedData）
    _, board_html = _fetch_html(url, deadline)
    if not board_html.strip():
        raise LinkError('这个秀米链接返回的是空白页面')
    data_url = _xiumius_data_url(board_html)
    _assert_public(data_url)
    # 2) 抓内容 JSON（gzip，httpx 自动解压）
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0)) as c:
            r = c.get(data_url, headers={'User-Agent': _UA_BROWSER,
                                         'Accept': 'application/json,text/plain,*/*'})
        if r.status_code >= 400:
            raise LinkError(f'这个秀米链接的内容数据打不开（HTTP {r.status_code}）')
        j = r.json()
    except LinkError:
        raise
    except Exception as e:
        raise LinkError(f'读取秀米内容数据失败（{e.__class__.__name__}），请稍后重试或上传资料')
    # 3) 取已渲染 HTML（真实正文 + 配图都在这里）
    try:
        ra = j['cubes'][0]['pages'][0]['layers'][0]['_comp']['_$raHTML']
    except (KeyError, IndexError, TypeError):
        raise LinkError('这个秀米链接的内容结构读不出来，请换一条或上传资料')
    if not ra or not ra.strip():
        raise LinkError('这个秀米链接读不到正文，可能是空排版，请换一条或上传资料')
    text, imgs = _html_fragment_text_imgs(ra)
    title = (j.get('title') or '').strip()
    if not text.strip():
        raise LinkError('这个秀米链接读不到正文，可能是以图片为主的排版，请上传资料')
    # 4) 图片下载（秀米图带 ?x-oss-process=… 查询参数，路径仍是 .jpg，可直链）
    if not imgs and j.get('cover'):
        imgs = [j['cover']]
    images = _download_images(imgs, data_url, dest_dir, deadline)
    truncated = len(text) > LINK_TEXT_MAX
    if truncated:
        text = text[:LINK_TEXT_MAX] + '\n…（正文过长，已截断）'
    return {'url': url, 'finalUrl': url, 'title': title, 'text': text,
            'images': images, 'imageTotal': len(imgs), 'truncated': truncated}


# ── 对外入口 ────────────────────────────────────────────────────────────────
def fetch_link(raw_url: str, dest_dir: Path) -> dict:
    """抓一条链接 → {'url','finalUrl','title','text','images':[Path],'imageTotal'}。

    失败一律抛 LinkError（中文人话），调用方转 422。
    """
    url = normalize_url(raw_url)
    deadline = time.monotonic() + TOTAL_DEADLINE
    # 秀米 board 是 JS 渲染的，静态抓取拿不到正文，走专属解析路径。
    if _is_xiumius(url):
        return _fetch_xiumius(url, dest_dir, deadline)
    wechat = 'mp.weixin.qq.com' in url
    # 微信文章：移动端 MicroMessenger UA 会被反爬验证码拦截（无 #js_content）。
    # 优先桌面浏览器 UA；若落到验证码 / 非文章页（kind != 'wechat'），回退移动端 UA 再试一次。
    ua_list = [_UA_BROWSER, _UA_WECHAT] if wechat else [_UA_BROWSER]
    final_url, html, ex, kind, text, imgs, title = '', '', None, '', '', [], ''
    for _ua in ua_list:
        final_url, html = _fetch_html(url, deadline, _ua)
        if not html.strip():
            continue
        ex = _Extractor()
        try:
            ex.feed(html)
            ex.close()
        except Exception:
            pass  # 残缺 HTML 也要尽量用已解析到的部分
        kind, text, imgs = ex.best()
        title = ex.page_title()
        if wechat and kind != 'wechat':
            continue  # 验证码 / 主页 / 失效页 → 换 UA 再试
        break
    # 静态抓取正文偏薄 → 用 headless 浏览器渲染 JS 后再抽一次（渐进增强，失败回落静态）
    if len(text) < 200 and not wechat:
        r_url, r_html = _fetch_rendered(url, _UA_BROWSER, deadline)
        if r_html:
            ex_r = _Extractor()
            try:
                ex_r.feed(r_html)
                ex_r.close()
            except Exception:
                pass
            k_r, t_r, i_r = ex_r.best()
            if len(t_r) > len(text):
                final_url, html, ex, kind, text, imgs, title = (
                    r_url, r_html, ex_r, k_r, t_r, i_r, ex_r.page_title())
    if not html.strip():
        raise LinkError('这条链接返回的是空白页面，读不到内容')

    # 微信公众号的「错误页」也返回 200 且带正文（实测：不存在的 /s/AAAA… 返回
    # 「参数错误」提示页，能读出 40+ 字，以前会被当成一篇文章直接生成一份废稿）。
    # 真正的文章一定有 #js_content 容器 —— 没有就不是文章，别猜。
    if 'mp.weixin.qq.com' in final_url and kind != 'wechat':
        for kw in ('参数错误', '已被发布者删除', '因违规无法查看', '该内容已被删除',
                   '系统繁忙', '访问过于频繁', '环境异常', '请使用微信扫描二维码'):
            if kw in html:
                raise LinkError(f'这篇公众号文章打不开（{kw}）。请确认链接是在浏览器里能正常打开的文章，'
                                '或直接上传方案 / 贴别家的活动页。')
        raise LinkError('这条微信链接不是一篇文章（可能是主页或失效地址），'
                        '请用「…/s/xxxx」这种具体文章链接，或直接上传方案。')
    if text and len(text) < 80 and re.search(r'(404|页面不存在|Not Found|找不到|出错|发生错误)', html, re.I):
        raise LinkError('这个页面看起来是「找不到 / 出错」页，读不到活动内容，请换一条链接')

    # 正文太短时，用 meta description 兜一下（部分活动页正文是 JS 渲染的）
    if len(text) < 40 and ex.meta_desc:
        text = _clean_text(ex.meta_desc)
    if not text.strip():
        if 'mp.weixin.qq.com' in final_url:
            # 图片型长图推文：正文烤在图里，静态无文字但图已拿到 → 放行，AI 走画报式排版
            if ex.is_wechat and imgs:
                pass
            else:
                raise LinkError('这篇公众号文章读不到正文：可能是已被删除、需要验证，'
                                '或作者设置了「仅关注后可见」。请换一篇公开文章，或直接上传资料。')
        else:
            raise LinkError('这个页面读不到正文（可能是靠 JS 动态渲染的）。'
                            '请换成能直接看到文字的活动页，或把方案存成 PDF 上传。')

    truncated = len(text) > LINK_TEXT_MAX
    if truncated:
        text = text[:LINK_TEXT_MAX] + '\n…（正文过长，已截断）'

    if not imgs and ex.og_image:
        imgs = [ex.og_image]
    images = _download_images(imgs, final_url, dest_dir, deadline)

    return {'url': url, 'finalUrl': final_url, 'title': title, 'text': text,
            'images': images, 'imageTotal': len(imgs), 'truncated': truncated}
