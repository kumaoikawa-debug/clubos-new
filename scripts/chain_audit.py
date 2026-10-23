#!/usr/bin/env python3
"""
全链路静态自查（Chain Audit）— 只读扫描，不发任何请求。

A. 前端调用的 /api/** 路径，后端是否真的注册了（坏链 = 404）
B. 前端调的 method 与后端允许的方法是否一致（405）
C. HTML/JS 内联事件引用的函数，是否真的有定义（点了没反应）
D. 各端导航 data-wv / data-tab 目标，是否有对应 view 容器（点了空白）
E. JS 渲染目标 $('#xxx') / getElementById('xxx') 的容器是否存在（写进空气）

用法:
  CLUBOS_DB_PATH=/tmp/audit_app.db MOCK_AI=1 CLUBOS_TEST_OPEN=1 \
    python scripts/chain_audit.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

SEG = "\x01"   # 单段动态占位
SEGS = "\x02"  # 多段动态占位
QUOTES = "'\"" + "`"


def frontend_files():
    out = []
    for pat in ("*.js", "*.html"):
        for f in sorted(STATIC.rglob(pat)):
            if "uploads" in f.parts or "demo" in f.parts or "vendor" in f.parts:
                continue
            out.append(f)
    return out


def strip_comments(src):
    """把 /* */ 与 // 注释清成空格（保留换行，行号不变）。

    不清会大量误报：代码注释里写的「/api/xxx 后端根本没有」「/api/club 鉴权路由」
    会被当成真实调用点，人工核一遍要花很久。字符串里的 http:// 不受影响。
    """
    out, i, n, state, q = [], 0, len(src), None, None
    while i < n:
        c = src[i]
        if state is None:
            if src.startswith("/*", i):
                state = "block"; out.append("  "); i += 2; continue
            if src.startswith("//", i):
                state = "line"; out.append("  "); i += 2; continue
            if c in "'\"`":
                state = "str"; q = c
            out.append(c); i += 1; continue
        if state == "str":
            if c == "\\":
                out.append("  "); i += 2; continue
            if c == q:
                state = None
            out.append("\n" if c == "\n" else c); i += 1; continue
        if state == "block":
            if src.startswith("*/", i):
                state = None; out.append("  "); i += 2; continue
            out.append("\n" if c == "\n" else " "); i += 1; continue
        # state == "line"：整行注释，遇换行必须退出，否则第一个 // 之后整个文件都被吞掉
        if c == "\n":
            state = None
        out.append("\n" if c == "\n" else " "); i += 1; continue
    return "".join(out)


def read_code(f):
    """JS 剥注释；HTML 原样返回（HTML 里的 // 可能是无引号 URL，剥了会误伤）。"""
    raw = f.read_text(encoding="utf-8", errors="ignore")
    return strip_comments(raw) if f.suffix == ".js" else raw


# ------------------------------------------------------------------ URL 提取
def extract_urls(line):
    """从一行里抽出 /api/... 的完整 URL 模板（支持引号拼接与 ${} 内嵌套引号）。

    返回 [(raw, normalized)]，normalized 里 ${...} / +expr+ 都变成 SEG 占位。
    """
    res = []
    i, n = 0, len(line)
    while i < n:
        if not line.startswith("/api/", i):
            i += 1
            continue
        start = i
        q = line[start - 1] if start > 0 else None
        if q not in QUOTES:
            q = None
        j, buf, brace = start, [], 0
        while j < n:
            c = line[j]
            if c == "\\" and q:
                buf.append(line[j:j + 2]); j += 2; continue
            if line.startswith("${", j):
                buf.append(SEG); brace = 1; j += 2
                while j < n and brace > 0:
                    if line[j] == "{":
                        brace += 1
                    elif line[j] == "}":
                        brace -= 1
                    j += 1
                continue
            if q and c == q:
                break
            buf.append(c); j += 1
        raw = "".join(buf)
        nxt = line[j + 1:] if q else ""
        while True:
            m = re.match(r"\s*\+\s*[^+]*?\+\s*(['\"`])([^'\"`]*)\1", nxt)
            if not m:
                break
            raw += SEG + m.group(2)
            nxt = nxt[m.end():]
        if re.match(r"\s*\+\s*[A-Za-z_$][\w$.\[\]]*\s*(?=[,)\];+]|$|\s)", nxt):
            raw += SEG
        raw = raw.split("?", 1)[0].split("#", 1)[0]
        if raw.startswith("/api/"):
            res.append((line[start:j + (1 if q else 0)], raw))
        i = j + 1
    return res


def scan_calls():
    out = []
    for f in frontend_files():
        text = read_code(f)
        for ln, line in enumerate(text.splitlines(), 1):
            urls = extract_urls(line)
            if not urls:
                continue
            mm = re.search(r"""method\s*:\s*['"]([A-Z]+)['"]""", line)
            method = mm.group(1) if mm else None
            for raw, norm in urls:
                out.append((str(f.relative_to(ROOT)), ln, raw, norm, method))
    return out


# ------------------------------------------------------------------ 后端路由
def load_backend():
    sys.path.insert(0, str(ROOT))
    import app as appmod  # noqa
    merged = {}
    for r in appmod.app.routes:
        if not hasattr(r, "methods"):
            continue
        p = re.sub(r"\{[^}:]+:path\}", SEGS, r.path)
        p = re.sub(r"\{[^}]+\}", SEG, p)
        merged.setdefault(p, set()).update(
            m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    return merged


def match_backend(norm_map, norm):
    if norm in norm_map:
        return norm_map[norm], False
    pat = re.compile("^" + re.escape(norm).replace(re.escape(SEG), r"[^/]+")
                     .replace(re.escape(SEGS), r".+") + "$")
    hits = [m for p, m in norm_map.items() if pat.match(p)]
    if hits:
        return set().union(*hits), True
    return None, False


# ------------------------------------------------------------------ C 事件
EVT_HTML = re.compile(r"""\son(click|change|input|submit|keyup)\s*=\s*["']([^"']+)["']""")
EVT_JS = re.compile(r"""on(?:click|change|input)\s*=\s*\\?["']([^"']+)["']""")
FN_CALL = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
BUILTIN = {"if", "for", "while", "switch", "return", "typeof", "catch",
           "function", "new", "await", "else", "do", "try", "Number",
           "String", "Boolean", "Array", "Object", "Date", "Math", "JSON",
           "parseInt", "parseFloat", "isNaN", "alert", "confirm", "prompt",
           "setTimeout", "setInterval", "encodeURIComponent",
           "decodeURIComponent", "encodeURI", "fetch", "Promise"}


def scan_defined():
    defined = set()
    for f in frontend_files():
        text = read_code(f)
        for m in re.finditer(r"function\s+([A-Za-z_$][\w$]*)", text):
            defined.add(m.group(1))
        for m in re.finditer(r"(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=", text):
            defined.add(m.group(1))
        for m in re.finditer(
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()",
                text):
            defined.add(m.group(1))
        for m in re.finditer(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?function",
                             text, re.M):
            defined.add(m.group(1))
    return defined


def scan_events(defined):
    missing, seen = [], set()
    for f in frontend_files():
        text = read_code(f)
        for ln, line in enumerate(text.splitlines(), 1):
            handlers = [m.group(2) for m in EVT_HTML.finditer(line)]
            handlers += [m.group(1) for m in EVT_JS.finditer(line)]
            if not handlers:
                continue
            for h in handlers:
                for fn in FN_CALL.findall(h):
                    if fn in BUILTIN or fn in defined:
                        continue
                    k = (fn, str(f))
                    if k in seen:
                        continue
                    seen.add(k)
                    missing.append((str(f.relative_to(ROOT)), ln, fn))
    return missing


# ------------------------------------------------------------------ D 导航
def scan_nav():
    navs, ids_by_end = [], {}
    for end in ("web", "club", "platform", "leader"):
        d = STATIC / end
        if not d.exists():
            continue
        ids = set()
        for f in sorted(d.glob("*.html")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            ids |= set(re.findall(r"""\sid=["']([\w-]+)["']""", text))
            for m in re.finditer(
                    r"""data-(?:wv|view|tab|page|panel|sec|t|cv)\s*=\s*["']([\w-]+)["']""",
                    text):
                navs.append((end, str(f.relative_to(ROOT)), m.group(1)))
        ids_by_end[end] = ids
    missing = [(e, f, t) for e, f, t in navs if t not in ids_by_end.get(e, ())]
    return missing, len(navs)


# ------------------------------------------------------------------ E 容器
SEL_RE = re.compile(r"""\$\(\s*['"]#([\w-]+)['"]""")
GETEL_RE = re.compile(r"""getElementById\(\s*['"]([\w-]+)['"]""")
QSEL_RE = re.compile(r"""querySelector(?:All)?\(\s*['"]#([\w-]+)['"]""")
ID_DECL = re.compile(r"""\sid=["']([\w-]+)["']""")


def scan_containers():
    declared = set()
    for f in frontend_files():
        text = read_code(f)
        declared |= set(ID_DECL.findall(text))
        declared |= set(re.findall(r"""\\?id=\\?["']([\w-]+)\\?["']""", text))
    missing, seen = [], set()
    for f in frontend_files():
        text = read_code(f)
        for ln, line in enumerate(text.splitlines(), 1):
            names = []
            for rx in (SEL_RE, GETEL_RE, QSEL_RE):
                names += rx.findall(line)
            for nm in names:
                if nm in declared or (nm, str(f)) in seen:
                    continue
                seen.add((nm, str(f)))
                missing.append((str(f.relative_to(ROOT)), ln, nm))
    return missing, len(declared)


# ------------------------------------------------------------------ 主流程
def dump(title, rows, fmt, limit=40):
    print(f"\n【{title}】 {len(rows)} 项")
    if not rows:
        print("   ✅ 无")
        return
    for r in rows[:limit]:
        print("   " + fmt(r))
    if len(rows) > limit:
        print(f"   ... 另有 {len(rows)-limit} 项")


def main():
    norm_map = load_backend()
    calls = scan_calls()
    bad_path, bad_method, seen = [], [], set()
    for f, ln, raw, norm, method in calls:
        key = (norm, method)
        if key in seen:
            continue
        seen.add(key)
        m, _ = match_backend(norm_map, norm)
        if m is None:
            bad_path.append((f, ln, raw, method))
        elif method and method not in m:
            bad_method.append((f, ln, raw, method, sorted(m)))

    defined = scan_defined()
    miss_fn = scan_events(defined)
    miss_nav, nav_total = scan_nav()
    miss_dom, id_total = scan_containers()

    W = 78
    print("=" * W)
    print("全链路静态自查（只读扫描）")
    print("=" * W)
    print(f"后端注册路由      : {len(norm_map)} 条（归一化后）")
    print(f"前端 API 调用点   : {len(calls)} 处")
    print(f"导航 data-* 目标  : {nav_total} 个")
    print(f"已声明元素 id     : {id_total} 个")
    print("-" * W)
    def short(s):
        s = str(s).replace("\x01", "{*}").replace("\x02", "{**}")
        return s if len(s) <= 110 else s[:107] + "..."

    dump("A. 后端没有这条路径（必 404）", bad_path,
         lambda r: f"{r[0]}:{r[1]}  {short(r[2])}  [{r[3] or 'GET'}]")
    dump("B. 路径存在但方法不允许（必 405）", bad_method,
         lambda r: f"{r[0]}:{r[1]}  {short(r[2])}  [{r[3]}] 后端仅允许 {r[4]}")
    dump("C. 内联事件调用未定义函数（点了没反应）", miss_fn,
         lambda r: f"{r[0]}:{r[1]}  -> {r[2]}()")
    dump("D. 导航目标无对应容器（点了空白）", miss_nav,
         lambda r: f"[{r[0]}] {r[1]}  目标 {r[2]}")
    dump("E. 渲染目标容器不存在（写进空气）", miss_dom,
         lambda r: f"{r[0]}:{r[1]}  #{r[2]}")
    total = len(bad_path) + len(bad_method) + len(miss_fn) + len(miss_nav) + len(miss_dom)
    print("\n" + "=" * W)
    print(f"合计可疑项: {total}")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
