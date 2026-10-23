"""商品目录：品牌 / 品类 / 服饰维度 / SKU 编码（2026-10-24）。

为什么单独成模块：ClubOS 的装备商城要面向多品牌上架（`products` 原本只有一个自由
文本的 category，表达不出「品牌 + 品类 + 人群 + 颜色 × 尺码」），而品牌/品类是
**跨商品共享的字典**，增删改都必须走同一处，否则同一个品牌会被建成两条（一个写
"ARC'TERYX"、一个写"始祖鸟"），列表按品牌筛选就永远筛不全。

数据模型对齐 Medusa / Saleor 的共识范式，但只取 ClubOS 真正用得到的部分：
  · 品牌（brand）与品类（category）是独立字典，商品用外键引用
  · 品类两层（服装 → 冲锋衣），够用且不必引入树形递归查询
  · 颜色 × 尺码是**规格的两个维度**，不是两个规格列表 ——
    2 色 × 3 码 = 6 个 SKU，而不是「炭灰/M/L」这种混在一维里的写法
  · SKU 编码由维度自动生成，不手填（手填在多品牌下必然撞号）

性别/人群与季节是商品级维度（整个商品一致），颜色尺码是规格级维度（每个 SKU 不同）。
"""
from __future__ import annotations
import re, sqlite3

# 人群：服饰电商标配。unisex 兜住「不分男女」的装备（登山杖、背包），
# 不能让运营为了必填而随便选一个。
GENDERS = (
    ('unisex', '中性'),
    ('male', '男款'),
    ('female', '女款'),
    ('kids', '儿童'),
)
GENDER_CN = dict(GENDERS)
GENDER_KEYS = {k for k, _ in GENDERS}

# 季节/系列：户外行业按「春夏 SS / 秋冬 FW」订货，另有全年常青款。
SEASONS = ('常青', '春夏', '秋冬')

_SLUG_STRIP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def slugify(text: str) -> str:
    """把品牌/品类名压成 SKU 里能用的片段。

    只保留中英文与数字：SKU 要能被复制到采购单、条码枪、快递面单里，
    空格和斜杠进了这些地方就是截断和乱码。中文保留是因为「始祖鸟」这类
    品牌名拼音化之后运营自己也对不上号。
    """
    s = _SLUG_STRIP.sub('', str(text or '')).strip().upper()
    return s[:12]


class ProductCatalogService:
    """品牌 / 品类的读写，以及 SKU 编码生成。

    所有方法都接受外部传入的 connection，不自己开事务 ——
    调用方（API 层）往往要在同一个事务里改商品再改字典。
    """

    # ------------------------------------------------------------------ 品牌
    def list_brands(self, c, *, include_inactive: bool = False):
        q = "SELECT b.*,(SELECT COUNT(*) FROM products p WHERE p.brand_id=b.id) product_count FROM product_brands b"
        if not include_inactive:
            q += " WHERE b.status='active'"
        return [dict(r) for r in c.execute(q + " ORDER BY b.sort,b.id").fetchall()]

    def create_brand(self, c, *, name: str, logo_url: str | None = None, sort: int = 0) -> int:
        name = str(name or '').strip()
        if not name:
            raise ValueError('品牌名不能为空')
        if c.execute('SELECT id FROM product_brands WHERE name=?', (name,)).fetchone():
            raise ValueError('品牌「%s」已存在' % name)
        c.execute('INSERT INTO product_brands(name,slug,logo_url,sort,status) VALUES(?,?,?,?,?)',
                  (name, slugify(name), (logo_url or None), int(sort or 0), 'active'))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def update_brand(self, c, brand_id: int, *, name=None, logo_url=None, sort=None, status=None):
        b = c.execute('SELECT * FROM product_brands WHERE id=?', (int(brand_id),)).fetchone()
        if not b:
            raise LookupError('品牌不存在')
        name = str(name).strip() if name is not None else b['name']
        if not name:
            raise ValueError('品牌名不能为空')
        dup = c.execute('SELECT id FROM product_brands WHERE name=? AND id<>?', (name, int(brand_id))).fetchone()
        if dup:
            raise ValueError('品牌「%s」已存在' % name)
        c.execute('UPDATE product_brands SET name=?,slug=?,logo_url=?,sort=?,status=? WHERE id=?',
                  (name, slugify(name),
                   (logo_url if logo_url is not None else b['logo_url']),
                   int(sort) if sort is not None else int(b['sort'] or 0),
                   str(status) if status is not None else b['status'],
                   int(brand_id)))

    def delete_brand(self, c, brand_id: int) -> dict:
        """删品牌前先看有没有商品在用它。

        直接删会让这些商品的 brand_id 变成悬空外键，列表里品牌列一片空白、
        筛选也筛不到 —— 比「删不掉」更难发现。所以默认把商品摘下来再删，
        并把受影响商品数如实回传，让前端能说清楚改了什么。
        """
        b = c.execute('SELECT id,name FROM product_brands WHERE id=?', (int(brand_id),)).fetchone()
        if not b:
            raise LookupError('品牌不存在')
        used = int(c.execute('SELECT COUNT(*) FROM products WHERE brand_id=?', (int(brand_id),)).fetchone()[0])
        c.execute('UPDATE products SET brand_id=NULL WHERE brand_id=?', (int(brand_id),))
        c.execute('DELETE FROM product_brands WHERE id=?', (int(brand_id),))
        return {'detachedProducts': used}

    # ------------------------------------------------------------------ 品类
    def list_categories(self, c, *, include_inactive: bool = False):
        q = """SELECT ct.*,
                 (SELECT name FROM product_categories pc WHERE pc.id=ct.parent_id) parent_name,
                 (SELECT COUNT(*) FROM products p WHERE p.category_id=ct.id) product_count
               FROM product_categories ct"""
        if not include_inactive:
            q += " WHERE ct.status='active'"
        return [dict(r) for r in c.execute(q + " ORDER BY ct.sort,ct.id").fetchall()]

    def create_category(self, c, *, name: str, parent_id: int | None = None, sort: int = 0) -> int:
        name = str(name or '').strip()
        if not name:
            raise ValueError('品类名不能为空')
        pid = int(parent_id) if parent_id else None
        # 父子各自判重，但允许「服装>背包」与「装备>背包」同名 —— 它们是不同品类。
        # SQLite 的 UNIQUE 遇到 parent_id IS NULL 不生效（NULL 不等于 NULL），
        # 所以唯一性只能在这里判，不能指望数据库约束。
        if c.execute('SELECT id FROM product_categories WHERE name=? AND IFNULL(parent_id,0)=?',
                     (name, pid or 0)).fetchone():
            raise ValueError('该层级下已有同名品类')
        if pid:
            pc = c.execute('SELECT id,parent_id FROM product_categories WHERE id=?', (pid,)).fetchone()
            if not pc:
                raise ValueError('上级品类不存在')
            if pc['parent_id']:
                raise ValueError('品类只支持两层，不能再往下一级建')
        c.execute('INSERT INTO product_categories(name,slug,parent_id,sort,status) VALUES(?,?,?,?,?)',
                  (name, slugify(name), pid, int(sort or 0), 'active'))
        return int(c.execute('SELECT last_insert_rowid()').fetchone()[0])

    def update_category(self, c, category_id: int, *, name=None, parent_id=None, sort=None, status=None):
        ct = c.execute('SELECT * FROM product_categories WHERE id=?', (int(category_id),)).fetchone()
        if not ct:
            raise LookupError('品类不存在')
        name = str(name).strip() if name is not None else ct['name']
        if not name:
            raise ValueError('品类名不能为空')
        pid = int(parent_id) if parent_id not in (None, '') else (ct['parent_id'])
        pid = int(pid) if pid else None
        if pid == int(category_id):
            raise ValueError('上级品类不能是自己')
        if c.execute('SELECT id FROM product_categories WHERE name=? AND IFNULL(parent_id,0)=? AND id<>?',
                     (name, pid or 0, int(category_id))).fetchone():
            raise ValueError('该层级下已有同名品类')
        # 已经有子品类的不能再挂到别人下面 —— 否则层级会变成三层
        if pid and c.execute('SELECT id FROM product_categories WHERE parent_id=?', (int(category_id),)).fetchone():
            raise ValueError('该品类下已有子品类，不能再挂上级')
        c.execute('UPDATE product_categories SET name=?,slug=?,parent_id=?,sort=?,status=? WHERE id=?',
                  (name, slugify(name), pid,
                   int(sort) if sort is not None else int(ct['sort'] or 0),
                   str(status) if status is not None else ct['status'],
                   int(category_id)))

    def delete_category(self, c, category_id: int) -> dict:
        ct = c.execute('SELECT id FROM product_categories WHERE id=?', (int(category_id),)).fetchone()
        if not ct:
            raise LookupError('品类不存在')
        kids = int(c.execute('SELECT COUNT(*) FROM product_categories WHERE parent_id=?', (int(category_id),)).fetchone()[0])
        # 有子品类的不能直接删：删了子品类的 parent_id 悬空，它们会「消失」在一级列表里
        # （列表按 parent 分组渲染，找不到父级就没地方显示）。
        if kids:
            raise ValueError('请先删除或移走它的 %d 个子品类' % kids)
        used = int(c.execute('SELECT COUNT(*) FROM products WHERE category_id=?', (int(category_id),)).fetchone()[0])
        c.execute('UPDATE products SET category_id=NULL WHERE category_id=?', (int(category_id),))
        c.execute('DELETE FROM product_categories WHERE id=?', (int(category_id),))
        return {'detachedProducts': used}

    # -------------------------------------------------------------- SKU 编码
    def build_sku(self, c, *, product_id: int, brand_id=None, category_id=None,
                  color: str = '', size: str = '') -> str:
        """按 品牌-品类-商品-颜色-尺码 生成 SKU。

        为什么不手填：多品牌之后「GEAR-001」这种号一定撞。维度拼出来的号
        人看不懂但机器不会重复，而撞号在采购入库、库存流水里是查不出的数据事故。
        商品段用商品 id 而不是商品名 —— 名字会改，id 不会。
        """
        parts = []
        b = None
        if brand_id:
            b = c.execute('SELECT slug,name FROM product_brands WHERE id=?', (int(brand_id),)).fetchone()
        parts.append((b['slug'] if b and b['slug'] else None) or 'NOBRAND')
        ct = None
        if category_id:
            ct = c.execute('SELECT slug,name FROM product_categories WHERE id=?', (int(category_id),)).fetchone()
        parts.append((ct['slug'] if ct and ct['slug'] else None) or 'GEN')
        parts.append('P%04d' % int(product_id))
        if str(color or '').strip():
            parts.append(slugify(color) or 'C')
        if str(size or '').strip():
            parts.append(slugify(size) or 'S')
        return '-'.join(parts)

    def ensure_unique_sku(self, c, sku: str, *, exclude_variant_id: int | None = None) -> str:
        """同号自动加后缀。

        运营可以手改 SKU，改成一个已存在的号在入库时会被 UNIQUE 约束直接拒，
        但那时整个提交已经做了一半。这里在写入前就排开：SKU-2 / SKU-3。
        """
        base = str(sku or '').strip() or 'SKU'
        candidate, n = base, 1
        while True:
            q = 'SELECT id FROM product_variants WHERE sku=?'
            args = [candidate]
            if exclude_variant_id:
                q += ' AND id<>?'
                args.append(int(exclude_variant_id))
            if not c.execute(q, args).fetchone():
                return candidate
            n += 1
            candidate = '%s-%d' % (base, n)

    # ------------------------------------------------------------ 维度规范化
    @staticmethod
    def normalize_gender(raw) -> str:
        # 中文别名一起认：CSV 是运营从 Excel 粘的，「男款 / 女款」比 male/female 常见得多。
        # 认不出来的值一律落 unisex —— 性别是筛选维度，错判成男款会让女顾客看不到商品。
        g = str(raw or '').strip().lower()
        if g in GENDER_KEYS:
            return g
        for k, cn in GENDERS:
            if g == cn.lower():
                return k
        return 'unisex'

    @staticmethod
    def normalize_color_hex(raw) -> str | None:
        """颜色色值只接受 #RGB / #RRGGBB，其余一律丢弃。

        色值是给 C 端颜色块直接当 background 用的；放过 "red" 这种单词或
        "rgb(1,2,3" 这种残缺值，前端色块就会变成透明或黑块。宁可没有色块，
        也不要一个错的色块。
        """
        s = str(raw or '').strip()
        if not s:
            return None
        if s[0] != '#':
            s = '#' + s
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", s):
            return s.upper()
        if re.fullmatch(r"#[0-9A-Fa-f]{3}", s):
            return ('#' + ''.join(ch * 2 for ch in s[1:])).upper()
        return None

    @staticmethod
    def normalize_season(raw) -> str | None:
        s = str(raw or '').strip()
        return s[:16] if s else None

    @staticmethod
    def normalize_material(raw) -> str | None:
        s = str(raw or '').strip()
        return s[:120] if s else None

    @staticmethod
    def normalize_series(raw) -> str | None:
        s = str(raw or '').strip()
        return s[:60] if s else None

    @staticmethod
    def normalize_dimension(raw) -> str | None:
        """颜色 / 尺码名：去空白并限长。

        这两个值会直接进 SKU，也会被当成 C 端按钮文案。限长是为了让
        SKU 不会长到条码打不下；不强制枚举是因为各品牌的码数体系不一样
        （S/M/L、38/39/40、均码），写死枚举只会逼运营乱填。
        """
        s = str(raw or '').strip()
        return s[:24] if s else None


catalog_service = ProductCatalogService()
