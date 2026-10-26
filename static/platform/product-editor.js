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
      整批重建会把它们冲掉，所以只提供「按颜色×尺码补生成」+ 跳转既有规格管理。
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

  function readColors(root) {
    return [...root.querySelectorAll('.plat-pe__chip')].map(chip => ({
      name: (chip.querySelector('.plat-pe__chipname').value || '').trim(),
      hex: platNormHex(chip.querySelector('.plat-pe__chiphex').value)
    }));
  }

  /* 手填色值的非法项（写了东西但不是 #RGB/#RRGGBB）——保存前用来拦一下，
     别把一个看不出错的串悄悄落库，C 端色块会渲染成透明。 */
  function badHexChips(root) {
    return [...root.querySelectorAll('.plat-pe__chiphex')].filter(el => {
      const raw = (el.value || '').trim();
      return raw && !platNormHex(raw);
    });
  }

  function sizesOf(root) {
    return (root.querySelector('#peSizes').value || '')
      .split(/[,，]/).map(s => s.trim()).filter(Boolean);
  }

  function mxPreview(root) {
    const colors = readColors(root).filter(c => c.name);
    const sizes = sizesOf(root);
    const box = root.querySelector('#peMxPreview');
    if (!box) return;
    const n = Math.max(1, colors.length) * Math.max(1, sizes.length);
    box.textContent = (colors.length || sizes.length)
      ? `将生成 ${colors.length || 1} 色 × ${sizes.length || 1} 码 = ${n} 个 SKU：` +
        (colors.slice(0, 3).map(c => c.name).join(' / ') || '默认色') +
        ' × ' + (sizes.slice(0, 6).join(',') || '均码') + (sizes.length > 6 ? ' 等' : '')
      : '未配置矩阵：只建一个「默认」规格，C 端照常可买';
  }

  /* 期初库存网格（2026-10-26 用户反馈）：不能手填总数，按 颜色×尺码 逐格填数量，
     期初库存=全部数量之和，实时汇总到「期初库存（自动汇总）」只读框。
     键用颜色 cid（稳定）+尺码名：改颜色名/删行都不会把已填数量串到别的颜色上。 */
  function qtyOf(st, cid, sz) { return (st.qty[cid] && st.qty[cid][sz]) || 0; }

  function paintQtyGrid(root, st, isEdit) {
    const grid = root.querySelector('#peQtyGrid');
    if (!grid) return;
    const colors = st.colors.filter(c => (c.name || '').trim());
    const sizes = sizesOf(root);
    let html = '';
    if (!colors.length && !sizes.length) {
      /* 没配矩阵：只有一个「默认」规格，数量框就是它的期初库存 */
      html = `<div class="plat-pe__qtyrow"><span class="plat-pe__qtyname">默认规格</span>
        <label class="plat-pe__qtycell">数量<input class="plat-pe__qtyin" type="number" min="0" step="1" data-cid="" data-size="" value="${qtyOf(st, '', '')}"></label></div>`;
    } else if (!colors.length) {
      /* 只填了尺码没填颜色：全部组合落在默认色上 */
      html = `<div class="plat-pe__qtyrow"><span class="plat-pe__qtyname">默认色</span>
        ${sizes.map(sz => `<label class="plat-pe__qtycell">${esc(sz)}<input class="plat-pe__qtyin" type="number" min="0" step="1" data-cid="" data-size="${esc(sz)}" value="${qtyOf(st, '', sz)}"></label>`).join('')}</div>`;
    } else {
      const szs = sizes.length ? sizes : [''];
      html = colors.map(c => `<div class="plat-pe__qtyrow">
        <span class="plat-pe__qtyname">${c.hex ? `<i style="background:${esc(c.hex)}"></i>` : ''}${esc(c.name)}</span>
        ${szs.map(sz => `<label class="plat-pe__qtycell">${esc(sz || '均码')}<input class="plat-pe__qtyin" type="number" min="0" step="1" data-cid="${esc(c.cid)}" data-size="${esc(sz)}" value="${qtyOf(st, c.cid, sz)}"></label>`).join('')}</div>`).join('');
    }
    grid.innerHTML = html || '<div class="sub">先添加颜色或尺码，再按格填数量</div>';
    updateStockTotal(root, st, isEdit);
  }

  /* 汇总口径与后端 _build_variant_matrix 严格一致：只统计当前矩阵里
     真正会生成的组合（已命名的颜色 × 尺码列表），改名/删行的残键不计数。 */
  function stockTotal(root, st) {
    const colors = st.colors.filter(c => (c.name || '').trim());
    const sizes = sizesOf(root);
    let t = 0;
    if (!colors.length && !sizes.length) {
      t = qtyOf(st, '', '');
    } else {
      const szs = sizes.length ? sizes : [''];
      if (colors.length) colors.forEach(c => szs.forEach(sz => { t += qtyOf(st, c.cid, sz); }));
      else szs.forEach(sz => { t += qtyOf(st, '', sz); });
    }
    return t;
  }

  function updateStockTotal(root, st, isEdit) {
    const t = stockTotal(root, st);
    if (isEdit) {
      const add = root.querySelector('#peAddTotal');
      if (add) add.textContent = t;
    } else {
      const el = root.querySelector('#peStock');
      if (el) el.value = t;
    }
  }

  /* 颜色 chip：色块预览（只读）+ 颜色名 + 色值文本框 + 删除。
     2026-10-25 用户要求「颜色只手动填写，不要系统色块选择器」——原来的
     <input type="color"> 会把浏览器/系统的取色盘顶出来，取色盘只能给近似色，
     运营手里有品牌色号（#2B2B2B 这种）时反而填不准。改成手填后，左边那个小方块
     退化成纯预览：填对了它显示成什么色，一眼可验，但它不可点、不弹任何东西。 */
  function chipHtml(c, i) {
    const hex = platNormHex(c.hex);
    return `<span class="plat-mx__chip plat-pe__chip" data-i="${i}" data-cid="${esc(c.cid || '')}">
      <span class="plat-mx__swatch${hex ? '' : ' plat-mx__swatch--empty'}"${hex ? ` style="background:${hex}"` : ''} aria-hidden="true"></span>
      <input class="plat-pe__chipname" type="text" value="${esc(c.name)}" placeholder="颜色名" aria-label="颜色名">
      <input class="plat-pe__chiphex" type="text" value="${esc(hex)}" placeholder="#RRGGBB" maxlength="7"
             spellcheck="false" autocomplete="off" aria-label="色值（手填，例如 #2B2B2B）">
      <button type="button" data-act="delcolor" aria-label="删除该颜色">×</button>
    </span>`;
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
      colors: [],            // {cid,name,hex} —— cid 是稳定键，改名/删行不串库存数量
      sizes: '',
      qty: {},               // 期初库存：cid（''=默认色/默认规格）→ 尺码（''=均码）→ 数量
      seq: 0
    };
    if (pid) {
      try {
        const data = await api(`/api/platform/products/${pid}/images`);
        st.cover.url = data.cover || '';
        (data.images || []).forEach(g => {
          st.gallery.push({ key: 'g' + (++st.seq), id: g.id, url: g.url, blob: null, isNew: false, del: false });
        });
      } catch (e) { /* 图片拉不到不影响编辑其他字段 */ }
      /* 已有规格回填矩阵：颜色 / 尺码只是「待追加的候选」，保存时矩阵会跳过
         已存在的组合，所以回填是安全的，同时让运营一眼看到现在有哪些维度。 */
      const seenColor = new Set();
      (existingVariants || []).forEach(v => {
        const c = (v.color || '').trim();
        if (c && !seenColor.has(c)) { seenColor.add(c); st.colors.push({ cid: 'c' + (++st.seq), name: c, hex: v.color_hex || '' }); }
      });
      const seenSize = [];
      (existingVariants || []).forEach(v => {
        const s = (v.size || '').trim();
        if (s && seenSize.indexOf(s) < 0) seenSize.push(s);
      });
      st.sizes = seenSize.join(', ');
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
                <em>${isEdit ? '库存按规格维护；下方新填的数量会计入「本次追加」' : '不能手填：由下方颜色×尺码的数量实时汇总'}</em></label>
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
            <div class="plat-pe__sec-h"><h3>规格矩阵</h3>
              <p>${isEdit ? '按 颜色 × 尺码 追加组合，已存在的组合会跳过，不会重置已调好的库存与条码。' : '按 颜色 × 尺码 一次生成所有 SKU 组合；不配则只建一个「默认」规格。'}</p></div>
            <div class="plat-pe__mx">
              <div>
                <div class="plat-mx__label">颜色 <em>可留空；色值手填 #RRGGBB，用于 C 端色块</em></div>
                <div class="plat-mx__chips" id="peColors"></div>
                <div style="margin-top:8px"><button class="btn ghost" type="button" data-act="addcolor">＋ 添加颜色</button></div>
              </div>
              <div>
                <div class="plat-mx__label">尺码 <em>逗号分隔，例如 S,M,L,XL 或 38,39,40,41</em></div>
                <input class="plat-mx__sizes" id="peSizes" placeholder="S, M, L, XL" value="${esc(st.sizes)}">
              </div>
              <div>
                <div class="plat-mx__label">每个规格的期初数量 <em>按颜色填每个尺码的件数（如 黑色 S:5 L:5 M:5）；期初库存自动汇总</em></div>
                <div class="plat-pe__qtygrid" id="peQtyGrid"></div>
              </div>
              <div class="plat-mx__preview" id="peMxPreview"></div>
              ${isEdit ? `<div class="plat-pe__varlist"><div class="plat-pe__label">已有规格（${(existingVariants || []).length}）· 本次追加 <b id="peAddTotal">0</b> 件</div>
                <div class="plat-pe__varchips">${(existingVariants || []).map(v => `<span class="plat-pe__varchip">${v.color_hex ? `<i style="background:${esc(v.color_hex)}"></i>` : ''}${esc(v.name)}<b>${v.stock}</b></span>`).join('') || '<span class="sub">还没有规格</span>'}</div>
                <button class="btn ghost sm" type="button" data-act="managevariants">管理已有规格（改库存 / 条码 / 单规格价）</button></div>` : ''}
            </div>
          </section>

          <section class="plat-pe__sec">
            <div class="plat-pe__sec-h"><h3>商品介绍</h3><p>C 端详情页展示的图文介绍，留空则该区块不显示。</p></div>
            <textarea id="peDesc" class="plat-pe__desc" rows="6" placeholder="例如：三层 GORE-TEX 面料，腋下拉链透气，适合高海拔多变天气…">${esc(prod ? prod.description : '')}</textarea>
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

    /* ---------- 颜色 ---------- */
    function paintColors() {
      root.querySelector('#peColors').innerHTML = st.colors.map(chipHtml).join('');
      mxPreview(root);
      paintQtyGrid(root, st, isEdit);
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
      const rawColors = readColors(root);
      const badHex = badHexChips(root);
      if (badHex.length) {
        toast('色值请填 #RRGGBB，例如 #2B2B2B');
        badHex[0].focus();
        try { badHex[0].select(); } catch (_) { /* 有些环境不让选 */ }
        return;
      }
      const colors = rawColors.filter(c => c.name);
      if (rawColors.length && !colors.length) { toast('颜色还没有填名称'); return; }
      const sizes = sizesOf(root);

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

      /* 期初库存按格汇总成 {颜色:{尺码:数量}}：与后端 _sku_stock_map 同构，
         键用「填写的名字」（后端会按同一套 normalize 归一），无颜色行落 '' 键。 */
      const namedColors = st.colors.filter(c => (c.name || '').trim());
      const skuStock = {};
      const qtyAt = (cid, sz) => (st.qty[cid] && st.qty[cid][sz]) || 0;
      if (namedColors.length) {
        const szs = sizes.length ? sizes : [''];
        namedColors.forEach(c => {
          const m = {};
          szs.forEach(sz => { const q = qtyAt(c.cid, sz); if (q > 0) m[sz] = q; });
          if (Object.keys(m).length) skuStock[c.name.trim()] = m;
        });
      } else if (sizes.length) {
        const m = {};
        sizes.forEach(sz => { const q = qtyAt('', sz); if (q > 0) m[sz] = q; });
        if (Object.keys(m).length) skuStock[''] = m;
      }

      let id = pid;
      try {
        if (!id) {
          const body = Object.assign({}, fields, {
            sku: (val('#peSku') || '').trim() || ('GEAR-' + Date.now()),
            stock: Number(val('#peStock') || 0)
          });
          if (colors.length || sizes.length) {
            body.colors = colors; body.sizes = sizes;
            if (Object.keys(skuStock).length) body.skuStock = skuStock;
          }
          if (msg) msg.textContent = '创建商品…';
          const r = await api('/api/platform/products', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          id = r && r.id;
          if (!id) throw new Error('商品创建失败：服务端没有返回 id');
        } else {
          if (msg) msg.textContent = '保存商品信息…';
          await api('/api/platform/products/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) });
          if (colors.length || sizes.length) {
            try {
              await api(`/api/platform/products/${id}/variants/matrix`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({ colors, sizes }, Object.keys(skuStock).length ? { skuStock } : {}))
              });
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
        toast(isEdit ? '商品已更新' : '商品已上架' + (colors.length && sizes.length ? ` · ${colors.length * sizes.length} 个规格` : ''));
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
      if (act === 'addcolor') {
        st.colors.push({ cid: 'c' + (++st.seq), name: '', hex: '' });
        dirty = true; paintColors();
        const names = root.querySelectorAll('.plat-pe__chipname');
        if (names.length) names[names.length - 1].focus();
        return;
      }
      if (act === 'delcolor') {
        const chip = btn.closest('.plat-pe__chip');
        const i = chip ? Number(chip.dataset.i) : -1;
        if (i >= 0) {
          const cid = st.colors[i] && st.colors[i].cid;
          if (cid) delete st.qty[cid];   // 删颜色连它名下已填的数量一起清，不留在汇总里
          st.colors.splice(i, 1); dirty = true; paintColors();
        }
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

    root.addEventListener('input', e => {
      if (e.target.classList.contains('plat-pe__qtyin')) {
        const cid = e.target.dataset.cid || '', sz = e.target.dataset.size || '';
        const v = Math.max(0, parseInt(e.target.value, 10) || 0);
        if (!st.qty[cid]) st.qty[cid] = {};
        st.qty[cid][sz] = v;
        dirty = true;
        updateStockTotal(root, st, isEdit);
        return;
      }
      if (e.target.closest('#peColors')) {
        const chip = e.target.closest('.plat-pe__chip');
        if (!chip) return;
        const i = Number(chip.dataset.i);
        if (e.target.classList.contains('plat-pe__chipname')) {
          st.colors[i].name = e.target.value;
          /* 改颜色名只刷数量网格的行名（数量按 cid 记，不会丢） */
          paintQtyGrid(root, st, isEdit);
        }
        else if (e.target.classList.contains('plat-pe__chiphex')) {
          /* 边打字边预览：只有能解析成色值时才刷色块，输一半的 “#2B” 不闪黑 */
          st.colors[i].hex = e.target.value.trim();
          const sw = chip.querySelector('.plat-mx__swatch');
          const hex = platNormHex(e.target.value);
          if (sw) {
            sw.style.background = hex || '';
            sw.classList.toggle('plat-mx__swatch--empty', !hex);
          }
          if (hex) e.target.classList.remove('is-bad');
          paintQtyGrid(root, st, isEdit);
        }
        dirty = true;
        mxPreview(root);
        return;
      }
      if (e.target.id === 'peSizes') { dirty = true; mxPreview(root); paintQtyGrid(root, st, isEdit); return; }
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
      const chip = el.closest('.plat-pe__chip');
      const i = chip ? Number(chip.dataset.i) : -1;
      if (i >= 0 && st.colors[i]) st.colors[i].hex = raw;
      const sw = chip && chip.querySelector('.plat-mx__swatch');
      if (sw) {
        sw.style.background = hex || '';
        sw.classList.toggle('plat-mx__swatch--empty', !hex);
      }
    });

    /* 初次绘制 */
    paintCover();
    paintGallery();
    paintColors();
    const nameEl = root.querySelector('#peName');
    if (nameEl) setTimeout(() => { try { nameEl.focus({ preventScroll: true }); } catch (_) { nameEl.focus(); } }, 30);
  };
})();
