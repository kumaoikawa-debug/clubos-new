/* 装备商品「单页编辑器」（总平台 · 2026-10-24）
   ──────────────────────────────────────────────────────────────────────
   为什么不再分三步弹窗：
   旧流程是「填基本信息 → 下一步配规格矩阵 → 保存 → 再弹图片管理器」，
   每改一个字段都要关掉三个弹层重来；运营建一个商品要开 3 个窗、点 8 次确定，
   图片还得等商品先落库才能传。用户原话：「所有信息都集合成一个页面，
   页面信息可以上下滑动那种，现在这样的交互太难用」。

   现在：一页到底，分区从上往下排（基础 → 分类 → 图片 → 规格 → 介绍），
   内容区自己滚动，底部一条固定操作栏；图片在保存前就能选、能裁、能排序，
   保存时一次性提交（新商品落库后自动补传图片）。

   关键实现约束（踩过的坑，改之前先读）：
   1. 新商品在保存前没有 id，所以图片只能先存在内存里（Blob + objectURL），
      保存拿到 id 之后再传 —— 不做「草稿商品」是为了避免脏数据。
   2. 图片裁剪复用 uxDialog 的 uxCropImage（它本身是个工具弹层，不是流程步骤），
      不在这里自己实现一套 canvas 交互。
   3. 所有点击走编辑器根节点上的一个委托监听：底部操作栏与内容区是兄弟节点，
      绑在内容区上会漏掉「保存」按钮（这个坑在图片管理器里已经踩过一次）。
   4. 编辑已有商品时，规格只做「追加组合」：已有的 SKU 可能已经调过库存和条码，
      整批重建会把它们冲掉，所以卡里已有组合只显示库存、不再给数量框。

   ★ 2026-10-26 第二轮（用户截图上原话「先建颜色，然后一个颜色后面可以建
     多个尺码库存」）：规格区改成「颜色卡片 → 卡内尺码行（尺码 + 期初数量）」。
     之前是「颜色列表 × 一条全局尺码串」的笛卡尔积，可现实里每个颜色的尺码并不
     一样（黑色 S/M/L、白色只有 M/L），强行套同一串尺码就会凭空多出一堆 0 库存
     的假 SKU，运营还得回头一个个删。现在每个颜色自带尺码表，后端按
     color.sizes 逐色展开（见 app.py 的 _parse_color_dims）。
*/
(function () {
  'use strict';

  const COVER_RATIO = 1;
  const DETAIL_RATIO = 3 / 4;

  const $id = id => document.getElementById(id);

  function optHtml(list, selected) {
    return (list || []).map(o =>
      `<option value="${esc(o.value)}"${String(o.value) === String(selected == null ? '' : selected) ? ' selected' : ''}>${esc(o.label)}</option>`
    ).join('');
  }

  /* 手填色值的非法项（写了东西但不是 #RGB/#RRGGBB）——保存前用来拦一下，
     别把一个看不出错的串悄悄落库，C 端色块会渲染成透明。 */
  function badHexChips(root) {
    return [...root.querySelectorAll('.plat-pe__chiphex')].filter(el => {
      const raw = (el.value || '').trim();
      return raw && !platNormHex(raw);
    });
  }

  const norm = s => String(s == null ? '' : s).trim();
  const intOf = v => Math.max(0, parseInt(v, 10) || 0);

  /* ---------- 规格口径（渲染 / 汇总 / 提交三处共用一套） ----------
     一个颜色的尺码来源只有一个：卡片里那几行尺码。
     尺码名全空时按「均码」处理（size=''），数量取该颜色第一行填的数字。 */
  function sizesOfColor(c) {
    const seen = Object.create(null), out = [];
    (c.sizes || []).forEach(s => {
      const nm = norm(s.name);
      if (!nm) return;
      const k = nm.toLowerCase();
      if (seen[k]) return;
      seen[k] = 1;
      out.push(nm);
    });
    return out;
  }

  function rowsOf(c) {
    const named = sizesOfColor(c);
    if (!named.length) {
      const first = (c.sizes || [])[0];
      return [{ size: '', qty: intOf(first && first.qty) }];
    }
    const qty = Object.create(null);
    (c.sizes || []).forEach(s => {
      const k = norm(s.name).toLowerCase();
      if (k && qty[k] == null) qty[k] = intOf(s.qty);
    });
    return named.map(sz => ({ size: sz, qty: qty[sz.toLowerCase()] || 0 }));
  }

  const existKey = (color, size) => norm(color).toLowerCase() + '|' + norm(size).toLowerCase();

  /* 当前配置会生成哪些 SKU（不含「没填颜色名」的半成品卡） */
  function skuList(st) {
    const out = [];
    st.colors.forEach(c => {
      const nm = norm(c.name);
      if (!nm) return;
      rowsOf(c).forEach(r => out.push({ cid: c.cid, color: nm, hex: platNormHex(c.hex), size: r.size, qty: r.qty }));
    });
    return out;
  }

  window.openProductEditor = async function (opts) {
    const cfg = opts || {};
    const pid = cfg.id ? Number(cfg.id) : null;

    await loadProductDicts();

    let prod = null, existingVariants = [];
    if (pid) {
      try {
        const list = await api('/api/platform/products');
        prod = (list || []).find(x => Number(x.id) === pid);
      } catch (e) { /* 走下面的统一报错 */ }
      if (!prod) { await showAlert({ title: '商品不存在', message: '列表可能已刷新，请关闭后重新进入商品中心。' }); return; }
      try { existingVariants = await api(`/api/platform/products/${pid}/variants`); } catch (e) { existingVariants = []; }
    }

    /* ---------- 状态 ---------- */
    const st = {
      cover: { url: '', blob: null, fromImageId: null },
      gallery: [],           // {key,id,url,blob,isNew,del}
      colors: [],            // {cid,name,hex,sizes:[{sid,name,qty}]} —— cid/sid 是稳定键，改名/删行不串数量
      defaultQty: 0,         // 一个颜色都不配时的「默认规格」期初数量
      seq: 0
    };

    /* 已有规格 → 键 (颜色|尺码)：编辑态用它标「已存在」，避免给已有组合再填一次数量
       （矩阵接口对已存在的组合是 skip，填了也不会生效，给了输入框就是骗人）。 */
    const existingMap = new Map();
    (existingVariants || []).forEach(v => {
      existingMap.set(existKey(v.color, v.size), Number(v.stock || 0));
    });

    if (pid) {
      try {
        const data = await api(`/api/platform/products/${pid}/images`);
        st.cover.url = data.cover || '';
        (data.images || []).forEach(g => {
          st.gallery.push({ key: 'g' + (++st.seq), id: g.id, url: g.url, blob: null, isNew: false, del: false });
        });
      } catch (e) { /* 图片拉不到不影响编辑其他字段 */ }
      /* 已有规格回填成「颜色卡 + 卡内尺码」：这样运营一眼看到现在每个颜色有哪几码，
         想加新码就在对应颜色里加一行，加错颜色也不会影响别的颜色。 */
      const byColor = new Map();
      (existingVariants || []).forEach(v => {
        const cn = norm(v.color);
        if (!cn) return;                                  // 「默认」占位规格不画成颜色
        const k = cn.toLowerCase();
        if (!byColor.has(k)) byColor.set(k, { cid: 'c' + (++st.seq), name: cn, hex: v.color_hex || '', sizes: [], locked: true });
        const card = byColor.get(k);
        const sn = norm(v.size);
        if (sn && !card.sizes.some(x => norm(x.name).toLowerCase() === sn.toLowerCase())) {
          card.sizes.push({ sid: 's' + (++st.seq), name: sn, qty: 0 });
        }
      });
      st.colors = [...byColor.values()];
    }

    const isEdit = !!pid;
    const title = isEdit ? ('编辑商品 · ' + (prod.name || '')) : '新建装备商品';

    /* ---------- 骨架 ---------- */
    const root = document.createElement('div');
    root.className = 'plat-pe';
    root.innerHTML = `
      <div class="plat-pe__sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <header class="plat-pe__bar">
          <button class="btn ghost" type="button" data-act="close">← 商品列表</button>
          <div class="plat-pe__title">${esc(title)}</div>
          <div class="plat-pe__baracts">
            <span class="plat-pe__msg" id="peMsg"></span>
            <button class="btn" type="button" data-act="save">${isEdit ? '保存修改' : '保存并上架'}</button>
          </div>
        </header>

        <div class="plat-pe__scroll">
          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>基础信息</h3><p>价格、最高佣金率、库存与仓库都在这里；佣金率是来源俱乐部的销售分成上限 —— 针对单个俱乐部调整的比例不能超过它。</p></div>
            <div class="plat-pe__grid">
              <label class="plat-pe__f plat-pe__f--wide"><span>商品名称 <i>*</i></span>
                <input id="peName" type="text" maxlength="80" placeholder="例如：Beta LT 冲锋衣" value="${esc(prod ? prod.name : '')}"></label>
              <label class="plat-pe__f"><span>款号 / 货号</span>
                <input id="peSku" type="text" placeholder="留空自动生成（GEAR-…）" value="${esc(prod ? prod.sku : '')}"${isEdit ? ' readonly' : ''}>
                <em>CSV 导入按款号归组，建议填品牌方给的货号</em></label>
              <label class="plat-pe__f"><span>零售价（元） <i>*</i></span>
                <input id="pePrice" type="number" min="0" step="0.01" value="${prod ? prod.price : ''}" placeholder="0.00"></label>
              <label class="plat-pe__f"><span>最高佣金率（%） <i>*</i></span>
                <input id="peCommission" type="number" min="0" max="100" step="0.1" value="${prod ? Math.round(Number(prod.commission_rate || 0) * 1000) / 10 : 8}">
                <em>俱乐部每单分成上限；平台按俱乐部单独调整时不能超过此值</em></label>
              <label class="plat-pe__f"><span>${isEdit ? '当前可售库存' : '期初库存（自动汇总）'}</span>
                <input id="peStock" type="number" min="0" step="1" value="${isEdit ? (prod.stock || 0) : 0}" readonly>
                <em>${isEdit ? '库存按规格维护；下方新填的数量计入「本次追加」' : '不能手填：由下方每个颜色的尺码数量实时汇总'}</em></label>
              <label class="plat-pe__f"><span>补货点</span>
                <input id="peReorder" type="number" min="0" step="1" value="${prod ? (prod.reorder_point == null ? 5 : prod.reorder_point) : 5}"></label>
              <label class="plat-pe__f"><span>仓库</span>
                <input id="peWarehouse" type="text" maxlength="40" placeholder="例如：成都主仓 / 双流三号仓" value="${esc(prod ? (prod.warehouse || '') : '')}">
                <em>记录这批货存在哪个仓</em></label>
              <label class="plat-pe__f"><span>状态</span>
                <select id="peStatus">
                  <option value="active"${!prod || prod.status === 'active' ? ' selected' : ''}>上架（C 端可见）</option>
                  <option value="inactive"${prod && prod.status !== 'active' ? ' selected' : ''}>下架</option>
                </select></label>
            </div>
          </section>

          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>分类维度</h3><p>品牌 / 品类 / 人群 / 季节 / 系列 / 面料 —— 六维决定 C 端筛选能不能筛到它。</p></div>
            <div class="plat-pe__grid">
              <label class="plat-pe__f"><span>品牌</span>
                <select id="peBrand"><option value="">不设品牌</option>${optHtml(prodBrandOptions(prod && prod.brand_id), prod && prod.brand_id)}</select></label>
              <label class="plat-pe__f"><span>品类</span>
                <select id="peCategory"><option value="">不分类</option>${optHtml(prodCatOptions(prod && prod.category_id), prod && prod.category_id)}</select></label>
              <label class="plat-pe__f"><span>人群</span>
                <select id="peGender">${optHtml(PROD_GENDERS, prod ? prod.gender : 'unisex')}</select></label>
              <label class="plat-pe__f"><span>季节</span>
                <input id="peSeason" type="text" placeholder="例如 2026 秋冬（留空为常年款）" value="${esc(prod ? prod.season : '')}"></label>
              <label class="plat-pe__f"><span>系列</span>
                <input id="peSeries" type="text" placeholder="例如 Beta 系列" value="${esc(prod ? prod.series : '')}"></label>
              <label class="plat-pe__f"><span>面料 / 材质</span>
                <input id="peMaterial" type="text" placeholder="例如 GORE-TEX 3L" value="${esc(prod ? prod.material : '')}"></label>
            </div>
          </section>

          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>商品图片</h3>
              <p>头图是 C 端卡片与详情页首图；详情图在详情页轮播。可直接从本地批量选，上传前自动裁到最佳比例（头图 1:1 / 详情 3:4），也可点「裁剪」手动微调。${isEdit ? '' : ' 保存后自动上传。'}</p></div>
            <div class="plat-pe__imgrow">
              <div class="plat-pe__imgcol">
                <div class="plat-pe__label">头图（主视觉）</div>
                <div id="peCover"></div>
              </div>
              <div class="plat-pe__imgcol plat-pe__imgcol--grow">
                <div class="plat-pe__label">详情图片
                  <button class="btn ghost sm" type="button" data-act="addgallery">＋ 批量添加</button>
                </div>
                <div id="peGallery" class="pim-grid"></div>
              </div>
            </div>
          </section>

          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>颜色 · 尺码 · 期初库存</h3>
              <p>${isEdit
                ? '每个颜色下面加它有的尺码，尺码后面填本次入库数量。已存在的「颜色 + 尺码」只显示现有库存（矩阵不会重复生成），要改库存请用下面的「管理已有规格」。'
                : '先建颜色，再在每个颜色下加这个颜色有的尺码和期初数量 —— 一个「颜色 + 尺码」就是一个 SKU。每个颜色的尺码可以不一样（黑色 S/M/L，白色只有 M/L）。不配则只建一个「默认」规格。'}</p></div>
            <div class="plat-pe__mx">
              <div class="plat-pe__specs" id="peColors"></div>
              <div class="plat-pe__specfoot">
                <button class="btn ghost" type="button" data-act="addcolor">＋ 添加颜色</button>
                <span class="plat-pe__specnote">颜色可留空；色值手填 #RRGGBB（C 端色块用），不用系统取色盘。</span>
              </div>
              <div class="plat-mx__preview" id="peMxPreview"></div>
              ${isEdit ? `<div class="plat-pe__varlist"><div class="plat-pe__label">已有规格（${(existingVariants || []).length}）· 本次追加 <b id="peAddTotal">0</b> 件</div>
                <div class="plat-pe__varchips">${(existingVariants || []).map(v => `<span class="plat-pe__varchip">${v.color_hex ? `<i style="background:${esc(v.color_hex)}"></i>` : ''}${esc(v.name)}<b>${v.stock}</b></span>`).join('') || '<span class="sub">还没有规格</span>'}</div>
                <button class="btn ghost sm" type="button" data-act="managevariants">管理已有规格（改库存 / 条码 / 单规格价）</button></div>` : ''}
            </div>
          </section>

          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>商品介绍</h3><p>C 端详情页展示的图文介绍，留空则该区块不显示。可插入图片，图片按插入顺序排在文字之间。</p></div>
            <div class="plat-pe__descbar">
              <button class="btn ghost sm" type="button" data-act="adddescimg">＋ 插入图片</button>
              <span class="plat-pe__deschint">支持 jpg / png / webp，可多选；大图自动压到 1280px 宽。</span>
            </div>
            <textarea id="peDesc" class="plat-pe__desc" rows="8" placeholder="例如：三层 GORE-TEX 面料，腋下拉链透气，适合高海拔多变天气…（也可点上方「插入图片」把详情图插进来）">${esc(prod ? prod.description : '')}</textarea>
            <div class="plat-pe__descpreview" id="peDescPreview"></div>
            <input id="peDescFile" type="file" accept="image/*" multiple hidden>
          </section>
        </div>

        <footer class="plat-pe__foot">
          <span class="sub">${isEdit ? '修改会立即生效' : '保存后商品立即上架（可随时下架）'}</span>
          <div class="plat-pe__footacts">
            <button class="btn ghost" type="button" data-act="close">取消</button>
            <button class="btn" type="button" data-act="save">${isEdit ? '保存修改' : '保存并上架'}</button>
          </div>
        </footer>
      </div>`;
    document.body.appendChild(root);

    let dirty = false;
    let closed = false;
    let session = null;
    const close = () => {
      if (closed) return;
      if (dirty) {
        showConfirm({ title: '放弃未保存的修改？', message: '这一页还有没保存的内容，关闭后不会保留。', confirmText: '放弃修改', danger: true })
          .then(ok => { if (ok) hardClose(); });
        return;
      }
      hardClose();
    };
    const hardClose = () => {
      if (closed) return;
      closed = true;
      session && session.release();
      /* 只回收本编辑器创建的 objectURL，别把别人的 revoke 掉 */
      st.gallery.forEach(g => { if (g.isNew && g.url) URL.revokeObjectURL(g.url); });
      root.remove();
      if (typeof cfg.onClose === 'function') cfg.onClose();
    };
    session = uxDialogSession(root, { onEscape: () => close() });

    /* ---------- 图片 ---------- */
    const coverBox = root.querySelector('#peCover');
    const galleryBox = root.querySelector('#peGallery');

    function galleryCoverUrl() { return st.cover.url || ''; }

    function paintCover() {
      coverBox.innerHTML = st.cover.url
        ? `<div class="pim-cover"><img src="${esc(st.cover.url)}" alt="">
             <div class="pim-cover__btns">
               <button class="btn" type="button" data-act="pickcover">重新上传</button>
               <button class="btn ghost" type="button" data-act="cropcover">裁剪</button>
             </div></div>`
        : `<button class="pim-cover pim-cover--empty" type="button" data-act="pickcover">＋ 上传头图</button>`;
    }

    function paintGallery() {
      const items = st.gallery.filter(g => !g.del);
      galleryBox.innerHTML = items.length
        ? items.map((g, i) => `<div class="pim-thumb" data-key="${g.key}">
             <img src="${esc(g.url)}" alt="">
             ${g.url === galleryCoverUrl() ? '<div class="pim-thumb__cover">头图</div>' : ''}
             <div class="pim-thumb__bar">
               ${g.url === galleryCoverUrl() ? '' : '<button class="btn ghost sm" type="button" data-act="gallery-cover">设为主图</button>'}
               <button class="btn ghost sm" type="button" data-act="gallery-crop">裁剪</button>
               <button class="btn ghost sm" type="button" data-act="gallery-del">删除</button>
             </div>
             <div class="pim-thumb__idx">${i + 1}</div>
           </div>`).join('')
        : '<div class="empty">还没有详情图片，点上方「＋ 批量添加」从本地上传（可一次选多张）。</div>';
    }

    function pickCover() {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = 'image/*';
      input.onchange = async () => {
        const f = input.files && input.files[0]; if (!f) return;
        const blob = await uxCropImage({ src: URL.createObjectURL(f), ratio: COVER_RATIO, title: '裁剪头图（1:1）' });
        if (!blob) return;
        setCoverBlob(blob);
      };
      input.click();
    }

    function setCoverBlob(blob) {
      const old = st.cover.url;
      if (old && old.indexOf('blob:') === 0) URL.revokeObjectURL(old);
      st.cover = { url: URL.createObjectURL(blob), blob, fromImageId: null };
      dirty = true; paintCover(); paintGallery();
    }

    async function cropCover() {
      if (!st.cover.url) return;
      const blob = await uxCropImage({ src: st.cover.url, ratio: COVER_RATIO, title: '裁剪头图（1:1）' });
      if (!blob) return;
      setCoverBlob(blob);
    }

    function pickGallery() {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = 'image/*'; input.multiple = true;
      input.onchange = async () => {
        const files = [...(input.files || [])];
        if (!files.length) return;
        const msg = root.querySelector('#peMsg');
        if (msg) msg.textContent = '正在裁切 ' + files.length + ' 张…';
        for (const f of files) {
          let blob;
          try { blob = await uxAutoCrop(f, DETAIL_RATIO); } catch (e) { blob = f; }
          st.gallery.push({ key: 'g' + (++st.seq), id: null, url: URL.createObjectURL(blob), blob, isNew: true, del: false });
        }
        dirty = true;
        if (msg) msg.textContent = '';
        paintGallery();
      };
      input.click();
    }

    async function cropGallery(key) {
      const g = st.gallery.find(x => x.key === key); if (!g) return;
      const blob = await uxCropImage({ src: g.url, ratio: DETAIL_RATIO, title: '裁剪详情图（3:4）' });
      if (!blob) return;
      if (g.url && g.url.indexOf('blob:') === 0) URL.revokeObjectURL(g.url);
      g.url = URL.createObjectURL(blob); g.blob = blob; g.isNew = true;
      /* 这张原本是头图的话，头图跟着一起换，不然会出现「头图还是旧图」的错觉 */
      if (st.cover.fromImageId && g.id && st.cover.fromImageId === g.id) {
        st.cover = { url: g.url, blob, fromImageId: null };
      }
      dirty = true; paintCover(); paintGallery();
    }

    function setCoverFromGallery(key) {
      const g = st.gallery.find(x => x.key === key); if (!g) return;
      st.cover = { url: g.url, blob: g.blob || null, fromImageId: g.blob ? null : (g.id || null) };
      dirty = true; paintCover(); paintGallery();
    }

    function delGallery(key) {
      const i = st.gallery.findIndex(x => x.key === key); if (i < 0) return;
      const g = st.gallery[i];
      if (g.isNew) {
        if (g.url && g.url.indexOf('blob:') === 0) URL.revokeObjectURL(g.url);
        st.gallery.splice(i, 1);
      } else {
        g.del = true;                      // 已有的图标记待删，保存时才真删
      }
      if (st.cover.fromImageId && g.id && st.cover.fromImageId === g.id) st.cover = { url: '', blob: null, fromImageId: null };
      dirty = true; paintCover(); paintGallery();
    }

    /* ---------- 商品介绍：文字 + 插图 ----------
       「插入图片」把图直接传到 /api/platform/uploads/image 拿 URL，再以 Markdown 图片行
       `![](url)` 插到光标处。这样新商品（还没有 id）也能立刻插图，保存时不需要补传；
       C 端按「整行是 ![](url) 就渲染成图片，否则当普通段落」解析（见 web.js pdDescHtml）。 */
    function downscaleImage(file, maxW) {
      maxW = maxW || 1280;
      return new Promise(resolve => {
        if (!file || !/^image\//.test(file.type)) { resolve(file); return; }
        const url = URL.createObjectURL(file);
        const im = new Image();
        im.onload = () => {
          try {
            const w = im.naturalWidth || 0, h = im.naturalHeight || 0;
            if (!w || w <= maxW) { URL.revokeObjectURL(url); resolve(file); return; }
            const nw = maxW, nh = Math.max(1, Math.round(h * maxW / w));
            const cv = document.createElement('canvas');
            cv.width = nw; cv.height = nh;
            cv.getContext('2d').drawImage(im, 0, 0, nw, nh);
            cv.toBlob(b => { URL.revokeObjectURL(url); resolve(b || file); }, 'image/jpeg', 0.9);
          } catch (e) { URL.revokeObjectURL(url); resolve(file); }
        };
        im.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
        im.src = url;
      });
    }

    function descPreviewHtml(text) {
      const lines = String(text || '').split('\n');
      let out = '', buf = [];
      const flush = () => { if (buf.length) { out += '<p>' + esc(buf.join('\n')) + '</p>'; buf = []; } };
      lines.forEach(line => {
        const m = line.trim().match(/^!\[\s*\]\(([^)]+)\)$/);
        if (m && /^(https?:\/\/|\/)/.test(m[1])) { flush(); out += '<img src="' + esc(m[1]) + '" alt="">'; }
        else if (!line.trim()) { flush(); }
        else buf.push(line);
      });
      flush();
      return out || '<span class="plat-pe__descempty">暂无介绍内容</span>';
    }

    function paintDescPreview() {
      const box = root.querySelector('#peDescPreview');
      const ta = root.querySelector('#peDesc');
      if (!box || !ta) return;
      box.innerHTML = descPreviewHtml(ta.value);
    }

    function insertAtCursor(ta, text) {
      /* 没聚焦在文本框时（点了按钮、文件框抢了焦点）就追加到末尾，
         否则 selectionStart 是 0，图会被插到整段文字最前面。 */
      const focused = (document.activeElement === ta);
      const start = (focused && ta.selectionStart != null) ? ta.selectionStart : ta.value.length;
      const end = (focused && ta.selectionEnd != null) ? ta.selectionEnd : ta.value.length;
      ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
      const pos = start + text.length;
      try { ta.selectionStart = ta.selectionEnd = pos; } catch (_) {}
      ta.focus();
      dirty = true;
      paintDescPreview();
    }

    function addDescImages() {
      const input = root.querySelector('#peDescFile');
      if (!input) return;
      input.value = '';                 /* 允许再次选同一个文件 */
      input.click();
    }

    function bindDescFile() {
      const input = root.querySelector('#peDescFile');
      if (!input) return;
      input.addEventListener('change', async () => {
        const files = [...(input.files || [])];
        if (!files.length) return;
        const ta = root.querySelector('#peDesc');
        const msg = root.querySelector('#peMsg');
        let done = 0;
        for (const f of files) {
          if (msg) msg.textContent = '上传插图 ' + (done + 1) + '/' + files.length + '…';
          let blob = f;
          try { blob = await downscaleImage(f, 1280); } catch (_) { blob = f; }
          const fd = new FormData();
          /* 保留真实格式：downscaleImage 只有「大图」才会转成 jpeg，
             小图原样返回；一律写 .jpg 会让 PNG 字节挂 .jpg 扩展名，
             服务端按扩展名给 Content-Type，C 端就解不出来。 */
          const btype = (blob && blob.type) || '';
          const ext = btype === 'image/png' ? '.png' : btype === 'image/webp' ? '.webp' : btype === 'image/gif' ? '.gif' : '.jpg';
          fd.append('file', blob, 'desc' + ext);
          try {
            const r = await api('/api/platform/uploads/image', { method: 'POST', body: fd });
            if (r && r.url) { insertAtCursor(ta, '\n![](' + r.url + ')\n'); done++; }
          } catch (e) { toast('插图上传失败：' + ((e && e.message) || '网络错误')); }
        }
        if (msg) msg.textContent = '';
        input.value = '';
        paintDescPreview();
      });
    }

    /* ---------- 规格：颜色卡 → 卡内尺码行 ----------
       渲染只在「增删卡/行」时整块重建；打字只更新 state + 局部刷新统计，
       否则每敲一个字都会把输入框换掉、焦点丢失（颜色名根本打不完整）。 */
    function cardCount(c) {
      const rows = rowsOf(c);
      const skus = rows.length;
      const fresh = rows.filter(r => !existingMap.has(existKey(norm(c.name), r.size)));
      const qty = fresh.reduce((a, r) => a + r.qty, 0);
      if (!norm(c.name)) return '填个颜色名';
      if (isEdit) return `${skus} 个尺码${fresh.length ? ` · 本次新增 ${fresh.length} 个` : ' · 都已存在'}`;
      return `${skus} 个尺码 · 共 ${qty} 件`;
    }

    function sizeRowHtml(c, s) {
      const cn = norm(c.name);
      const sn = norm(s.name);
      const isExist = !!cn && existingMap.has(existKey(cn, sn));
      return `<div class="plat-pe__sizerow${isExist ? ' plat-pe__sizerow--locked' : ''}" data-sid="${esc(s.sid)}">
        ${isExist
          ? `<span class="plat-pe__sizename plat-pe__sizename--static">${esc(sn || '均码')}</span>`
          : `<input class="plat-pe__sizename" type="text" value="${esc(s.name)}" placeholder="尺码，如 M / 38" aria-label="尺码" title="留空＝均码（这个颜色只有一个规格）">`}
        ${isExist
          ? `<span class="plat-pe__have" title="该规格已存在，库存请用「管理已有规格」修改">已有 ${existingMap.get(existKey(cn, sn))} 件</span>`
          : `<label class="plat-pe__qtycell">期初 <input class="plat-pe__qtyin" type="number" min="0" step="1" value="${intOf(s.qty)}" aria-label="期初数量"></label>`}
        ${isExist ? '' : '<button class="plat-pe__sdel" type="button" data-act="delsize" aria-label="删除该尺码">×</button>'}
      </div>`;
    }

    function cardHtml(c) {
      const hex = platNormHex(c.hex);
      const rows = c.sizes || [];
      /* locked＝这个颜色来自已有规格：颜色名/色值改了也只会追加出一组同义新 SKU
         （矩阵对已有组合是 skip），所以这里只读，改它们请走「管理已有规格」。
         卡里仍然可以加新尺码 —— 「追加」才是编辑态唯一的正向动作。 */
      const lock = !!c.locked;
      const lockTip = '已有规格的颜色名 / 色值请用「管理已有规格」修改；这里只能加新尺码';
      return `<div class="plat-pe__chip plat-pe__ccard${lock ? ' plat-pe__ccard--locked' : ''}" data-cid="${esc(c.cid)}">
        <div class="plat-pe__chead">
          <span class="plat-mx__swatch${hex ? '' : ' plat-mx__swatch--empty'}"${hex ? ` style="background:${hex}"` : ''} aria-hidden="true"></span>
          <input class="plat-pe__chipname" type="text" value="${esc(c.name)}" placeholder="颜色名，例如 黑色" aria-label="颜色名"${lock ? ` readonly title="${lockTip}"` : ''}>
          <input class="plat-pe__chiphex" type="text" value="${esc(hex)}" placeholder="#RRGGBB" maxlength="7"
                 spellcheck="false" autocomplete="off" aria-label="色值（手填，例如 #2B2B2B）"${lock ? ' readonly' : ''}>
          <span class="plat-pe__ccount">${esc(cardCount(c))}</span>
          ${lock ? '<span class="plat-pe__locktag">已有</span>'
                 : '<button class="plat-pe__cdel" type="button" data-act="delcolor" aria-label="删除该颜色">×</button>'}
        </div>
        <div class="plat-pe__sizerows">
          ${rows.length ? rows.map(s => sizeRowHtml(c, s)).join('')
            : `<div class="plat-pe__sizeempty">这个颜色还没有尺码 —— 不填尺码就只有「${esc(norm(c.name) || '该颜色')}」一个均码规格</div>`}
        </div>
        <div class="plat-pe__sizeadd">
          <input class="plat-pe__bulkin" type="text" data-cid="${esc(c.cid)}" placeholder="S,M,L 或 38（回车 / 点添加）" aria-label="批量添加尺码">
          <button class="btn ghost sm" type="button" data-act="addsize">＋ 添加尺码</button>
        </div>
      </div>`;
    }

    /* 一个颜色都没有时的兜底卡：不配矩阵也得能填期初库存（否则商品建出来就是 0 件） */
    function defaultCardHtml() {
      return `<div class="plat-pe__ccard plat-pe__ccard--default">
        <div class="plat-pe__chead">
          <span class="plat-mx__swatch plat-mx__swatch--empty" aria-hidden="true"></span>
          <span class="plat-pe__cdname">默认规格</span>
          <span class="plat-pe__ccount">不配颜色 / 尺码时只建这一个</span>
        </div>
        <div class="plat-pe__sizerows">
          <div class="plat-pe__sizerow">
            <span class="plat-pe__sizename plat-pe__sizename--static">均码</span>
            <label class="plat-pe__qtycell">期初 <input class="plat-pe__qtyin" type="number" min="0" step="1" data-default="1" value="${intOf(st.defaultQty)}" aria-label="期初数量"></label>
          </div>
        </div>
      </div>`;
    }

    function paintSpecs() {
      const box = root.querySelector('#peColors');
      if (!box) return;
      const cards = st.colors.map(cardHtml);
      if (!cards.length) cards.push(defaultCardHtml());
      box.innerHTML = cards.join('');
      syncSpecMeta();
    }

    /* 打字时只刷「卡右上角统计 / 预览条 / 库存汇总」，不动输入框本身 */
    function syncSpecMeta() {
      root.querySelectorAll('.plat-pe__ccard[data-cid]').forEach(card => {
        const c = st.colors.find(x => x.cid === card.dataset.cid);
        const el = card.querySelector('.plat-pe__ccount');
        if (c && el) el.textContent = cardCount(c);
      });
      mxPreview();
      updateStockTotal();
    }

    function existingFresh() {
      const skus = skuList(st);
      const fresh = skus.filter(s => !existingMap.has(existKey(s.color, s.size)));
      return { skus, fresh, exist: skus.length - fresh.length };
    }

    function stockTotal() {
      const { skus, fresh } = existingFresh();
      if (!skus.length) return intOf(st.defaultQty);
      return fresh.reduce((a, s) => a + s.qty, 0);
    }

    function updateStockTotal() {
      const t = stockTotal();
      if (isEdit) {
        const add = root.querySelector('#peAddTotal');
        if (add) add.textContent = t;
      } else {
        const el = root.querySelector('#peStock');
        if (el) el.value = t;
      }
    }

    function mxPreview() {
      const box = root.querySelector('#peMxPreview');
      if (!box) return;
      const { skus, fresh, exist } = existingFresh();
      if (!skus.length) {
        const q = intOf(st.defaultQty);
        box.textContent = '未配置颜色：只建一个「默认」规格' + (q > 0 ? `，期初 ${q} 件` : '，C 端照常可买');
        return;
      }
      const groups = [];
      st.colors.forEach(c => {
        const nm = norm(c.name);
        if (!nm) return;
        groups.push(nm + ' ' + rowsOf(c).map(r => r.size || '均码').join('/'));
      });
      const brief = groups.slice(0, 3).join('；') + (groups.length > 3 ? ' 等' : '');
      const nul = st.colors.filter(c => norm(c.name)).length;
      const qty = stockTotal();
      if (!isEdit) {
        box.textContent = `将生成 ${nul} 个颜色 · ${skus.length} 个 SKU · 期初共 ${qty} 件 —— ${brief}`;
      } else if (fresh.length) {
        box.textContent = `本次将新增 ${fresh.length} 个 SKU · 共 ${qty} 件${exist ? `；另有 ${exist} 个已存在会跳过` : ''} —— ${brief}`;
      } else {
        box.textContent = `这 ${skus.length} 个 SKU 都已存在，不会重复生成；要改库存请用「管理已有规格」 —— ${brief}`;
      }
    }

    /* 卡内的批量输入：逗号/空格分都行，重复尺码自动跳过；回车或点「添加尺码」触发 */
    function addSizesFromInput(input) {
      const cid = input && input.dataset ? input.dataset.cid : '';
      const c = st.colors.find(x => x.cid === cid);
      if (!c) return;
      const parts = (input.value || '').split(/[,，、;；\s]+/).map(x => x.trim()).filter(Boolean);
      if (!parts.length) return;
      /* 批量加真实尺码时，先丢掉卡片里「未命名的均码占位行」：
         新建颜色会自带一行空尺码（留着就是均码规格），如果这里再批量加
         S/M/L，会变成「均码 + S + M + L」四行、并多出一个 0 库存的均码 SKU，
         跟用户「先建颜色、再往这个颜色下加几个尺码」的心智模型冲突。 */
      c.sizes = c.sizes.filter(s => norm(s.name));
      parts.forEach(nm => {
        const k = nm.toLowerCase();
        if (!c.sizes.some(x => norm(x.name).toLowerCase() === k)) {
          c.sizes.push({ sid: 's' + (++st.seq), name: nm, qty: 0 });
        }
      });
      input.value = '';
      dirty = true;
      paintSpecs();
      const again = root.querySelector(`.plat-pe__bulkin[data-cid="${cid}"]`);
      if (again) again.focus();
    }

    /* ---------- 保存 ---------- */
    function val(sel) { const el = root.querySelector(sel); return el ? el.value : ''; }

    async function uploadCover(id, blob) {
      const fd = new FormData();
      fd.append('file', blob, 'cover.jpg');
      return api(`/api/platform/products/${id}/cover`, { method: 'POST', body: fd });
    }

    async function uploadGallery(id, blobs) {
      const fd = new FormData();
      blobs.forEach((b, i) => fd.append('files', b, 'img' + i + '.jpg'));
      return api(`/api/platform/products/${id}/images/batch`, { method: 'POST', body: fd });
    }

    async function save() {
      const msg = root.querySelector('#peMsg');
      const name = (val('#peName') || '').trim();
      const price = Number(val('#pePrice') || 0);
      if (!name) { toast('请填写商品名称'); root.querySelector('#peName').focus(); return; }
      if (!(price > 0)) { toast('请填写有效零售价'); root.querySelector('#pePrice').focus(); return; }
      const badHex = badHexChips(root);
      if (badHex.length) {
        toast('色值请填 #RRGGBB，例如 #2B2B2B');
        badHex[0].focus();
        try { badHex[0].select(); } catch (_) { /* 有些环境不让选 */ }
        return;
      }
      const namedColors = st.colors.filter(c => norm(c.name));
      if (st.colors.length && !namedColors.length) { toast('颜色还没有填名称'); return; }
      /* 两张卡取同一个颜色名 → 库存键会撞在一起，后端也会建重复 SKU，保存前先拦 */
      const seenName = Object.create(null);
      for (const c of namedColors) {
        const k = norm(c.name).toLowerCase();
        if (seenName[k]) { toast(`颜色「${norm(c.name)}」重复了，请合并成一张卡`); return; }
        seenName[k] = 1;
      }

      const colors = namedColors.map(c => ({ name: norm(c.name), hex: platNormHex(c.hex), sizes: sizesOfColor(c) }));
      /* 期初库存按「颜色 → 尺码」逐格提交，与后端 _sku_stock_map 同构；
         编辑态跳过已存在的组合（矩阵对它们是 skip，塞了也不生效）。 */
      const skuStock = {};
      namedColors.forEach(c => {
        const nm = norm(c.name);
        const m = {};
        rowsOf(c).forEach(r => {
          if (existingMap.has(existKey(nm, r.size))) return;
          if (r.qty > 0) m[r.size] = r.qty;
        });
        if (Object.keys(m).length) skuStock[nm] = m;
      });
      if (!colors.length) {
        const q = intOf(st.defaultQty);
        if (q > 0) skuStock[''] = { '': q };
      }

      const buttons = [...root.querySelectorAll('[data-act="save"]')];
      buttons.forEach(b => { b.disabled = true; b.textContent = '保存中…'; });
      if (msg) msg.textContent = '';

      const fields = {
        name,
        price,
        description: (val('#peDesc') || ''),
        gender: val('#peGender') || 'unisex',
        status: val('#peStatus') || 'active',
        reorderPoint: Number(val('#peReorder') || 0),
        brand_id: val('#peBrand') ? Number(val('#peBrand')) : null,
        category_id: val('#peCategory') ? Number(val('#peCategory')) : null,
        season: (val('#peSeason') || '').trim() || null,
        series: (val('#peSeries') || '').trim() || null,
        material: (val('#peMaterial') || '').trim() || null,
        warehouse: (val('#peWarehouse') || '').trim() || null,
        commission_rate: Number(val('#peCommission') || 0) / 100
      };

      const skuPlan = skuList(st).length;
      let id = pid;
      try {
        if (!id) {
          const body = Object.assign({}, fields, {
            sku: (val('#peSku') || '').trim() || ('GEAR-' + Date.now()),
            stock: Number(val('#peStock') || 0)
          });
          if (colors.length || Object.keys(skuStock).length) {
            if (colors.length) { body.colors = colors; body.sizes = []; }
            if (Object.keys(skuStock).length) body.skuStock = skuStock;
          }
          if (msg) msg.textContent = '创建商品…';
          const r = await api('/api/platform/products', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          id = r && r.id;
          if (!id) throw new Error('商品创建失败：服务端没有返回 id');
        } else {
          if (msg) msg.textContent = '保存商品信息…';
          await api('/api/platform/products/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) });
          if (colors.length) {
            try {
              const r = await api(`/api/platform/products/${id}/variants/matrix`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({ colors, sizes: [] }, Object.keys(skuStock).length ? { skuStock } : {}))
              });
              if (msg) msg.textContent = '';
              if (r && r.created) toast(`已追加 ${r.created} 个规格` + (r.skipped ? `，跳过已存在 ${r.skipped} 个` : ''));
            } catch (e) { toast('规格追加失败：' + (e.message || '')); }
          }
        }

        /* 图片：先传新增的详情图（拿到 id），再处理删除与头图 —— 顺序不能反：
           如果先删，头图回落可能落到一张马上要被删掉的图上。 */
        const news = st.gallery.filter(g => g.isNew && !g.del);
        if (news.length) {
          if (msg) msg.textContent = `上传图片 ${news.length} 张…`;
          const r = await uploadGallery(id, news.map(g => g.blob));
          (r.urls || []).forEach((u, i) => { if (news[i]) { news[i].url = u; news[i].isNew = false; } });
        }
        const dels = st.gallery.filter(g => g.id && g.del);
        for (const g of dels) {
          try { await api(`/api/platform/products/${id}/images/${g.id}`, { method: 'DELETE' }); } catch (e) { /* 已删过就算了 */ }
        }
        if (st.cover.blob) {
          if (msg) msg.textContent = '上传头图…';
          await uploadCover(id, st.cover.blob);
        } else if (st.cover.fromImageId) {
          try {
            await api(`/api/platform/products/${id}/images/${st.cover.fromImageId}`, {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setCover: true })
            });
          } catch (e) { /* 头图设置失败不阻断保存 */ }
        }

        dirty = false;
        toast(isEdit ? '商品已更新' : '商品已上架' + (skuPlan ? ` · ${skuPlan} 个规格` : ''));
        hardClose();
        if (typeof loadProducts === 'function') loadProducts();
      } catch (e) {
        if (msg) msg.textContent = '';
        buttons.forEach(b => { b.disabled = false; b.textContent = isEdit ? '保存修改' : '保存并上架'; });
        await showAlert({ title: isEdit ? '保存失败' : '上架失败', message: e.message || '未知错误' });
      }
    }

    /* ---------- 事件（统一委托到根节点） ---------- */
    root.addEventListener('click', e => {
      const btn = e.target.closest('[data-act]');
      if (!btn) {
        if (e.target === root) close();
        return;
      }
      const act = btn.dataset.act;
      if (act === 'close') { close(); return; }
      if (act === 'save') { save(); return; }
      if (act === 'pickcover') { pickCover(); return; }
      if (act === 'cropcover') { cropCover(); return; }
      if (act === 'addgallery') { pickGallery(); return; }
      if (act === 'adddescimg') { addDescImages(); return; }

      const card = btn.closest('.plat-pe__ccard[data-cid]');
      const ci = card ? st.colors.findIndex(x => x.cid === card.dataset.cid) : -1;
      const color = ci >= 0 ? st.colors[ci] : null;

      if (act === 'addcolor') {
        st.colors.push({ cid: 'c' + (++st.seq), name: '', hex: '', sizes: [{ sid: 's' + (++st.seq), name: '', qty: 0 }] });
        dirty = true; paintSpecs();
        const names = root.querySelectorAll('.plat-pe__chipname');
        if (names.length) names[names.length - 1].focus();
        return;
      }
      if (act === 'delcolor') {
        if (color) { st.colors.splice(ci, 1); dirty = true; paintSpecs(); }
        return;
      }
      if (act === 'addsize') {
        if (!color) return;
        const bulk = card.querySelector('.plat-pe__bulkin');
        if (bulk && (bulk.value || '').trim()) { addSizesFromInput(bulk); return; }
        color.sizes.push({ sid: 's' + (++st.seq), name: '', qty: 0 });
        dirty = true; paintSpecs();
        const rows = root.querySelectorAll(`.plat-pe__ccard[data-cid="${color.cid}"] .plat-pe__sizename`);
        if (rows.length) rows[rows.length - 1].focus();
        return;
      }
      if (act === 'delsize') {
        const row = btn.closest('.plat-pe__sizerow');
        if (!color || !row) return;
        const si = color.sizes.findIndex(s => s.sid === row.dataset.sid);
        if (si >= 0) { color.sizes.splice(si, 1); dirty = true; paintSpecs(); }
        return;
      }
      if (act === 'managevariants') {
        if (typeof manageVariants === 'function') manageVariants(pid);
        return;
      }
      const thumb = btn.closest('.pim-thumb');
      const key = thumb ? thumb.dataset.key : '';
      if (act === 'gallery-cover') { setCoverFromGallery(key); return; }
      if (act === 'gallery-crop') { cropGallery(key); return; }
      if (act === 'gallery-del') { delGallery(key); return; }
    });

    /* 卡内批量加尺码：回车即落（不用先点按钮再点输入框） */
    root.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      const el = e.target;
      if (!el || !el.classList || !el.classList.contains('plat-pe__bulkin')) return;
      e.preventDefault();
      addSizesFromInput(el);
    });

    root.addEventListener('input', e => {
      if (e.target && e.target.id === 'peDesc') { dirty = true; paintDescPreview(); return; }
      const card = e.target.closest('.plat-pe__ccard[data-cid]');
      if (card) {
        const ci = st.colors.findIndex(x => x.cid === card.dataset.cid);
        if (ci < 0) return;
        const c = st.colors[ci];
        if (e.target.classList.contains('plat-pe__chipname')) {
          c.name = e.target.value;
          dirty = true; syncSpecMeta();
          return;
        }
        if (e.target.classList.contains('plat-pe__chiphex')) {
          /* 边打字边预览：只有能解析成色值时才刷色块，输一半的 “#2B” 不闪黑 */
          c.hex = e.target.value.trim();
          const hex = platNormHex(e.target.value);
          const sw = card.querySelector('.plat-mx__swatch');
          if (sw) {
            sw.style.background = hex || '';
            sw.classList.toggle('plat-mx__swatch--empty', !hex);
            sw.style.borderStyle = '';
          }
          if (hex) e.target.classList.remove('is-bad');
          dirty = true; syncSpecMeta();
          return;
        }
        const row = e.target.closest('.plat-pe__sizerow');
        if (row) {
          const si = (c.sizes || []).findIndex(s => s.sid === row.dataset.sid);
          if (si < 0) return;
          if (e.target.classList.contains('plat-pe__sizename')) {
            c.sizes[si].name = e.target.value;
            dirty = true; syncSpecMeta();
            return;
          }
          if (e.target.classList.contains('plat-pe__qtyin')) {
            c.sizes[si].qty = intOf(e.target.value);
            dirty = true; syncSpecMeta();
            return;
          }
        }
        if (e.target.classList.contains('plat-pe__bulkin')) { dirty = true; return; }
        return;
      }
      if (e.target.dataset && e.target.dataset.default) {
        st.defaultQty = intOf(e.target.value);
        dirty = true; syncSpecMeta();
        return;
      }
      dirty = true;
    });

    /* 失焦时把色值补成规范写法：2b2b2b / #2B2B2B / #abc → #2B2B2B / #AABBCC。
       放在这里而不是 input 事件里，是因为边打字边改会把人还没写完的字符吃掉。
       写错了就标红留着让用户改 —— 直接清空等于「我填的色号凭空消失了」。 */
    root.addEventListener('focusout', e => {
      const el = e.target;
      if (!el || !el.classList || !el.classList.contains('plat-pe__chiphex')) return;
      const raw = (el.value || '').trim();
      const hex = platNormHex(raw);
      if (hex) { el.value = hex; el.classList.remove('is-bad'); }
      else if (raw) el.classList.add('is-bad');
      else el.classList.remove('is-bad');
      const card = el.closest('.plat-pe__ccard');
      const ci = card ? st.colors.findIndex(x => x.cid === card.dataset.cid) : -1;
      if (ci >= 0) st.colors[ci].hex = raw;
      const sw = card && card.querySelector('.plat-mx__swatch');
      if (sw) {
        sw.style.background = hex || '';
        sw.classList.toggle('plat-mx__swatch--empty', !hex);
      }
    });

    /* 初次绘制 */
    paintCover();
    paintGallery();
    paintSpecs();
    paintDescPreview();
    bindDescFile();
    const nameEl = root.querySelector('#peName');
    if (nameEl) setTimeout(() => { try { nameEl.focus({ preventScroll: true }); } catch (_) { nameEl.focus(); } }, 30);
  };
})();
