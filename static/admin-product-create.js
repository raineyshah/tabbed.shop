(function () {
    'use strict';

    const APC_DEFAULT_MADE_IN = [
        'Argentina', 'Australia', 'Austria', 'Belgium', 'Brazil', 'Canada', 'Chile', 'China',
        'Colombia', 'Czech Republic', 'Denmark', 'England', 'Finland', 'France', 'Germany',
        'Greece', 'Hungary', 'India', 'Indonesia', 'Ireland', 'Israel', 'Italy', 'Japan',
        'Malaysia', 'Mexico', 'Netherlands', 'New Zealand', 'Norway', 'Peru', 'Philippines',
        'Poland', 'Portugal', 'Romania', 'Singapore', 'South Africa', 'South Korea', 'Spain',
        'Sweden', 'Switzerland', 'Thailand', 'Turkey', 'United States', 'Vietnam',
    ];

    let adminCatalogBundlePromise = null;
    function invalidateAdminCatalogBundle() {
        adminCatalogBundlePromise = null;
    }

    async function getAdminCatalogBundle() {
        if (!adminCatalogBundlePromise) {
            adminCatalogBundlePromise = (async () => {
                try {
                    const [tagsRes, catsRes, brandsRes, certsRes, madeInRes] = await Promise.all([
                        fetch('/api/product-attribute-tags', { credentials: 'same-origin' }).then((r) => r.json()),
                        fetch('/api/categories', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.json()),
                        fetch('/api/admin/reference/brands', { credentials: 'same-origin', cache: 'no-store' }).then((r) => r.json()),
                        fetch('/api/admin/reference/certifications', {
                            credentials: 'same-origin',
                            cache: 'no-store',
                        }).then((r) => r.json()),
                        fetch('/api/made_in', { credentials: 'same-origin' }).then((r) => r.json()),
                    ]);

                    const categoryTree = Array.isArray(catsRes.categories) ? catsRes.categories : [];
                    const fromApiCat = categoryTree
                        .map((c) => {
                            if (c && typeof c === 'object' && c.name != null) return String(c.name).trim();
                            if (typeof c === 'string') return c.trim();
                            return '';
                        })
                        .filter(Boolean);
                    const catalogCategories = Array.from(new Set(fromApiCat)).sort((a, b) =>
                        a.localeCompare(b, undefined, { sensitivity: 'base' })
                    );

                    const brandRows = brandsRes.brands || [];
                    const brandMeta = {};
                    const catalogBrandNames = brandRows
                        .map((r) => {
                            const n = String(r.name || '').trim();
                            if (!n) return '';
                            brandMeta[n] = {
                                id: r.id,
                                has_image: !!r.has_image,
                            };
                            return n;
                        })
                        .filter(Boolean)
                        .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

                    const certRowsRaw = certsRes.certifications || [];
                    const certMeta = {};
                    const catalogCerts = certRowsRaw
                        .map((c) => {
                            const name = String(c.name || '').trim();
                            if (!name) return null;
                            certMeta[name] = {
                                id: c.id,
                                has_image: !!c.has_image,
                            };
                            return {
                                id: c.id,
                                name,
                                has_image: !!c.has_image,
                                image_filename: null,
                            };
                        })
                        .filter(Boolean);

                    const madeWith = [...(tagsRes.made_with || [])].sort((a, b) =>
                        a.localeCompare(b, undefined, { sensitivity: 'base' })
                    );
                    const madeWithout = [...(tagsRes.made_without || [])].sort((a, b) =>
                        a.localeCompare(b, undefined, { sensitivity: 'base' })
                    );
                    const attributes = [...(tagsRes.attributes || [])].sort((a, b) =>
                        a.localeCompare(b, undefined, { sensitivity: 'base' })
                    );

                    const fromApiMi = madeInRes.made_in || [];
                    const merged = new Set([...APC_DEFAULT_MADE_IN, ...fromApiMi]);
                    const madeInCountries = Array.from(merged).sort((a, b) =>
                        a.localeCompare(b, undefined, { sensitivity: 'base' })
                    );

                    return {
                        categoryTree,
                        catalogCategories,
                        catalogBrandNames,
                        brandMeta,
                        apcCatalogCertifications: catalogCerts,
                        certMeta,
                        madeWith,
                        madeWithout,
                        attributes,
                        madeInCountries,
                    };
                } catch (e) {
                    console.error('Admin catalog bundle fetch failed:', e);
                    return {
                        categoryTree: [],
                        catalogCategories: [],
                        catalogBrandNames: [],
                        brandMeta: {},
                        apcCatalogCertifications: [],
                        certMeta: {},
                        madeWith: [],
                        madeWithout: [],
                        attributes: [],
                        madeInCountries: Array.from(new Set(APC_DEFAULT_MADE_IN)).sort((a, b) =>
                            a.localeCompare(b, undefined, { sensitivity: 'base' })
                        ),
                    };
                }
            })();
        }
        return adminCatalogBundlePromise;
    }

    window.tabbedAdminWarmReferenceCatalogs = async function () {
        invalidateAdminCatalogBundle();
        await getAdminCatalogBundle();
    };

    /** Set by the last `bootAdminForm` call; legacy single refresh (prefer ``__tabbedAdminRefreshReferencePickers``). */
    let adminRefreshReferencePickers = null;

    function tabbedCertApiName(c) {
        if (c == null) return '';
        if (typeof c === 'string') return String(c).trim();
        return String((c && (c.name || c.certification_name)) || '').trim();
    }

    function bootAdminForm(P, opts) {
        const UI = typeof TabbedCatalogUI !== 'undefined' ? TabbedCatalogUI : null;
        const mode = opts.mode || 'add';
        const sheetMode = !!(opts && opts.sheetMode);
        const getSheetProductId =
            opts && typeof opts.getProductId === 'function' ? opts.getProductId : null;
        const formId = opts.formId;
        const alertsId = opts.alertsId || 'admin-add-alerts';

        function E(id) {
            return document.getElementById(P + id);
        }

        const formEl = document.getElementById(formId);
        if (!formEl) return;

        let apcProductNewImageObjectUrl = null;

        function revokeApcProductNewPreview() {
            if (apcProductNewImageObjectUrl) {
                URL.revokeObjectURL(apcProductNewImageObjectUrl);
                apcProductNewImageObjectUrl = null;
            }
            const w = E('productNewImagePreviewWrap');
            const im = E('productNewImagePreview');
            if (w) w.hidden = true;
            if (im) {
                im.removeAttribute('src');
                im.removeAttribute('alt');
            }
        }

        const apcTagContainers = {
            madeWithInput: [],
            madeWithoutInput: [],
            attributesInput: [],
            certifications: [],
        };

        let apcCatalogMadeWith = [];
        let apcCatalogMadeWithout = [];
        let apcCatalogAttributes = [];
        /** @type {{name: string, image_filename: string|null}[]} */
        let apcCatalogCertifications = [];

        let apcCatalogCategories = [];
        /** @type {Array<{slug: string, name: string, subcategories: {slug: string, name: string}[]}>} */
        let apcCategoryTree = [];
        let apcSelectedCategory = '';
        let apcSelectedSubcategory = '';
        let apcCatalogBrands = [];
        let apcSelectedBrand = '';
        /** @type {Record<string, { id: number, has_image: boolean }>} */
        let apcBrandMetaByName = {};
        /** @type {Record<string, { id: number, has_image: boolean }>} */
        let apcCertMetaByName = {};

        let apcCatalogMadeInCountries = [];
        let apcSelectedMadeIn = '';

        function apcBrandLogoSrc(name) {
            const m = apcBrandMetaByName[name];
            if (m && m.id && m.has_image) {
                return '/api/admin/reference/brands/' + m.id + '/image?v=' + encodeURIComponent(String(m.id));
            }
            return '';
        }

        function collapseContributePanelExpand(panelEl) {
            if (!panelEl) return;
            const header = panelEl.querySelector('.contribute-attr-header-sidebar');
            const slotSearch = panelEl.querySelector('.contribute-expand-search');
            const slotAdd = panelEl.querySelector('.contribute-expand-add');
            if (slotSearch) slotSearch.hidden = true;
            if (slotAdd) slotAdd.hidden = true;
            if (header) header.classList.remove('contribute-attr-header-sidebar--expanded');
        }

        function getPanelRoot() {
            if (sheetMode) {
                var row = formEl.closest('[data-admin-product-row]');
                if (row) return row;
            }
            return formEl;
        }

        function collapseAllContributePanelExpands() {
            getPanelRoot().querySelectorAll('[data-contribute-panel]').forEach(collapseContributePanelExpand);
        }

        function initContributePanelHeaders() {
            getPanelRoot().querySelectorAll('[data-contribute-panel]').forEach((panel) => {
                const header = panel.querySelector('.contribute-attr-header-sidebar');
                const slotSearch = panel.querySelector('.contribute-expand-search');
                const slotAdd = panel.querySelector('.contribute-expand-add');
                const btnSearch = panel.querySelector('.contribute-header-tool[data-tool="search"]');
                const btnAdd = panel.querySelector('.contribute-header-tool[data-tool="add"]');

                function syncExpand() {
                    const open = (slotSearch && !slotSearch.hidden) || (slotAdd && !slotAdd.hidden);
                    if (header) header.classList.toggle('contribute-attr-header-sidebar--expanded', !!open);
                }

                if (btnSearch && slotSearch) {
                    btnSearch.addEventListener('click', (e) => {
                        e.preventDefault();
                        if (slotAdd) slotAdd.hidden = true;
                        slotSearch.hidden = !slotSearch.hidden;
                        syncExpand();
                        if (!slotSearch.hidden) {
                            const inp = slotSearch.querySelector('input');
                            if (inp) {
                                inp.focus();
                                inp.select();
                            }
                        }
                    });
                }
                if (btnAdd && slotAdd) {
                    btnAdd.addEventListener('click', (e) => {
                        e.preventDefault();
                        if (slotSearch) slotSearch.hidden = true;
                        slotAdd.hidden = !slotAdd.hidden;
                        syncExpand();
                        if (!slotAdd.hidden) {
                            const inp = slotAdd.querySelector('.contribute-attr-new-input');
                            if (inp) inp.focus();
                        }
                    });
                }
            });
        }

        function getColorForTag(tag, existingColors) {
            return UI ? UI.getColorForTag(tag, existingColors) : '#888';
        }

        function apcAttrKindCatalog(kind) {
            if (kind === 'made_with') return apcCatalogMadeWith;
            if (kind === 'made_without') return apcCatalogMadeWithout;
            return apcCatalogAttributes;
        }

        function apcEnsureInCatalog(kind, value) {
            const arr = apcAttrKindCatalog(kind);
            if (!arr.includes(value)) {
                arr.push(value);
                arr.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
            }
        }

        function apcRenderPicker(kind) {
            const listElId =
                kind === 'made_with' ? 'contribute-made-with-picker'
                : kind === 'made_without' ? 'contribute-made-without-picker'
                : 'contribute-attributes-picker';
            const searchInputId =
                kind === 'made_with' ? 'search-contribute-made-with'
                : kind === 'made_without' ? 'search-contribute-made-without'
                : 'search-contribute-attributes';
            const container = E(listElId);
            const searchEl = E(searchInputId);
            if (!container) return;

            const localSearch = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            const catalog = apcAttrKindCatalog(kind);
            const selectedKey =
                kind === 'made_with' ? 'madeWithInput'
                : kind === 'made_without' ? 'madeWithoutInput'
                : 'attributesInput';
            const selected = apcTagContainers[selectedKey];
            const sectionColors = [];

            container.innerHTML = '';
            catalog.forEach((value) => {
                if (selected.includes(value)) return;
                if (localSearch && !value.toLowerCase().includes(localSearch)) return;

                const color = getColorForTag(value, sectionColors);
                sectionColors.push(color);
                const cell = document.createElement('div');
                cell.className = 'sidebar-filter-item';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                const swatch = document.createElement('div');
                swatch.className = 'tab-color';
                swatch.style.backgroundColor = color;
                const span = document.createElement('span');
                span.textContent = value;
                cell.appendChild(cb);
                cell.appendChild(swatch);
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcAddTagToInput(selectedKey, value);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderSelectedColumn(kind) {
            const containerId =
                kind === 'made_with' ? 'contribute-selected-made-with'
                : kind === 'made_without' ? 'contribute-selected-made-without'
                : 'contribute-selected-attributes';
            const containerKey =
                kind === 'made_with' ? 'madeWithInput'
                : kind === 'made_without' ? 'madeWithoutInput'
                : 'attributesInput';
            const container = E(containerId);
            if (!container) return;

            const list = apcTagContainers[containerKey];
            const sectionColors = [];
            container.innerHTML = '';
            list.forEach((value, index) => {
                const color = getColorForTag(value, sectionColors);
                sectionColors.push(color);
                const item = document.createElement('div');
                item.className = 'sidebar-selected-chip';
                const left = document.createElement('div');
                left.className = 'sidebar-chip-left';
                const swatch = document.createElement('div');
                swatch.className = 'sidebar-chip-color';
                swatch.style.backgroundColor = color;
                const span = document.createElement('span');
                span.textContent = value;
                left.appendChild(swatch);
                left.appendChild(span);
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'sidebar-chip-remove';
                btn.textContent = '✕';
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    list.splice(index, 1);
                    apcRefreshColumn(kind);
                });
                item.appendChild(left);
                item.appendChild(btn);
                container.appendChild(item);
            });
        }

        function apcRefreshColumn(kind) {
            apcRenderPicker(kind);
            apcRenderSelectedColumn(kind);
        }

        function apcRefreshAttributeColumns() {
            apcRefreshColumn('made_with');
            apcRefreshColumn('made_without');
            apcRefreshColumn('attributes');
        }

        function apcCategoryDisplayName(name) {
            if (!name) return '';
            return String(name).trim();
        }

        function apcSubcategoriesForSelectedMain() {
            const main = apcSelectedCategory;
            if (!main) return [];
            const row = apcCategoryTree.find((c) => c && c.name === main);
            if (!row || !Array.isArray(row.subcategories)) return [];
            return row.subcategories.map((s) => String(s.name || '').trim()).filter(Boolean);
        }

        function apcSyncSubcategoryHidden() {
            const el = E('subcategory');
            if (el) el.value = apcSelectedSubcategory || '';
        }

        function apcSetSubcategory(value) {
            apcSelectedSubcategory = (value && String(value).trim()) || '';
            apcSyncSubcategoryHidden();
            apcRenderSubcategoryPicker();
            apcRenderSubcategorySelected();
        }

        function apcRenderSubcategoryPicker() {
            const container = E('contribute-subcategory-picker');
            const searchEl = E('search-contribute-subcategory');
            if (!container) return;
            const q = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            container.innerHTML = '';
            if (!apcSelectedCategory) return;
            apcSubcategoriesForSelectedMain().forEach((name) => {
                if (name === apcSelectedSubcategory) return;
                if (q && !name.toLowerCase().includes(q)) return;
                const cell = document.createElement('div');
                cell.className = 'sidebar-filter-item';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                const span = document.createElement('span');
                span.textContent = name;
                cell.appendChild(cb);
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcSetSubcategory(name);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderSubcategorySelected() {
            const container = E('contribute-selected-subcategory');
            if (!container) return;
            container.innerHTML = '';
            const v = (apcSelectedSubcategory || '').trim();
            if (!v) return;
            const valid = apcSubcategoriesForSelectedMain();
            const inCatalog = valid.includes(v);
            const item = document.createElement('div');
            item.className = 'sidebar-selected-chip';
            const left = document.createElement('div');
            left.className = 'sidebar-chip-left';
            const span = document.createElement('span');
            span.textContent = inCatalog ? v : `${v} (not in catalog)`;
            left.appendChild(span);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sidebar-chip-remove';
            btn.textContent = '✕';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                apcSetSubcategory('');
            });
            item.appendChild(left);
            item.appendChild(btn);
            container.appendChild(item);
        }

        function apcRefreshSubcategorySelect(preserveValue) {
            const valid = apcSubcategoriesForSelectedMain();
            const prev =
                preserveValue != null
                    ? String(preserveValue).trim()
                    : (apcSelectedSubcategory || '').trim();
            if (!apcSelectedCategory) {
                apcSelectedSubcategory = '';
            } else if (prev && valid.includes(prev)) {
                apcSelectedSubcategory = prev;
            } else if (prev && apcSelectedCategory) {
                apcSelectedSubcategory = prev;
            } else {
                apcSelectedSubcategory = '';
            }
            apcSyncSubcategoryHidden();
            apcRenderSubcategoryPicker();
            apcRenderSubcategorySelected();
        }

        function apcSetCategory(value) {
            apcSelectedCategory = (value && String(value).trim()) || '';
            const el = E('mainCategory');
            if (el) el.value = apcSelectedCategory;
            apcRenderCategoryPicker();
            apcRenderCategorySelected();
            apcRefreshSubcategorySelect('');
        }

        function apcRenderCategoryPicker() {
            const container = E('contribute-category-picker');
            const searchEl = E('search-contribute-category');
            if (!container) return;
            const q = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            container.innerHTML = '';
            apcCatalogCategories.forEach((slug) => {
                if (slug === apcSelectedCategory) return;
                const label = apcCategoryDisplayName(slug);
                if (q && !slug.toLowerCase().includes(q) && !label.toLowerCase().includes(q)) return;
                const cell = document.createElement('div');
                cell.className = 'sidebar-filter-item';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                const span = document.createElement('span');
                span.textContent = label;
                cell.appendChild(cb);
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcSetCategory(slug);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderCategorySelected() {
            const container = E('contribute-selected-category');
            if (!container) return;
            container.innerHTML = '';
            if (!apcSelectedCategory) return;
            const item = document.createElement('div');
            item.className = 'sidebar-selected-chip';
            const left = document.createElement('div');
            left.className = 'sidebar-chip-left';
            const span = document.createElement('span');
            span.textContent = apcCategoryDisplayName(apcSelectedCategory);
            left.appendChild(span);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sidebar-chip-remove';
            btn.textContent = '✕';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                apcSetCategory('');
            });
            item.appendChild(left);
            item.appendChild(btn);
            container.appendChild(item);
        }

        async function apcLoadCategoryCatalog() {
            try {
                const b = await getAdminCatalogBundle();
                apcCategoryTree = Array.isArray(b.categoryTree) ? b.categoryTree : [];
                apcCatalogCategories = [...b.catalogCategories];
            } catch (e) {
                console.error('Failed to load categories:', e);
                apcCategoryTree = [];
                apcCatalogCategories = [];
            }
            apcRenderCategoryPicker();
            apcRenderCategorySelected();
            apcRefreshSubcategorySelect();
        }

        function apcSetBrand(value) {
            apcSelectedBrand = (value && String(value).trim()) || '';
            const el = E('brandName');
            if (el) el.value = apcSelectedBrand;
            apcRenderBrandPicker();
            apcRenderBrandSelected();
        }

        function apcRenderBrandPicker() {
            const container = E('contribute-brand-picker');
            const searchEl = E('search-contribute-brand');
            if (!container) return;
            const q = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            container.innerHTML = '';
            apcCatalogBrands.forEach((value) => {
                if (value === apcSelectedBrand) return;
                if (q && !value.toLowerCase().includes(q)) return;
                const logoSrc = apcBrandLogoSrc(value);
                const cell = document.createElement('div');
                cell.className =
                    'sidebar-filter-item' + (logoSrc ? ' sidebar-filter-item--brand' : '');
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                cell.appendChild(cb);
                if (logoSrc) {
                    const img = document.createElement('img');
                    img.className = 'sidebar-brand-logo sidebar-picker-ref-img';
                    img.src = logoSrc;
                    img.alt = value;
                    cell.appendChild(img);
                } else {
                    const swatch = document.createElement('div');
                    swatch.className = 'tab-color';
                    swatch.style.backgroundColor = getColorForTag(value);
                    cell.appendChild(swatch);
                }
                const span = document.createElement('span');
                span.textContent = value;
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcSetBrand(value);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderBrandSelected() {
            const container = E('contribute-selected-brand');
            if (!container) return;
            container.innerHTML = '';
            if (!apcSelectedBrand) return;
            const logoSrc = apcBrandLogoSrc(apcSelectedBrand);
            const item = document.createElement('div');
            item.className = 'sidebar-selected-chip';
            const left = document.createElement('div');
            left.className = 'sidebar-chip-left' + (logoSrc ? ' sidebar-chip-left--brand' : '');
            if (logoSrc) {
                const img = document.createElement('img');
                img.className = 'sidebar-brand-chip-img sidebar-picker-ref-img';
                img.src = logoSrc;
                img.alt = apcSelectedBrand;
                left.appendChild(img);
            } else {
                const swatch = document.createElement('div');
                swatch.className = 'sidebar-chip-color';
                swatch.style.backgroundColor = getColorForTag(apcSelectedBrand);
                left.appendChild(swatch);
            }
            const span = document.createElement('span');
            span.textContent = apcSelectedBrand;
            left.appendChild(span);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sidebar-chip-remove';
            btn.textContent = '✕';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                apcSetBrand('');
            });
            item.appendChild(left);
            item.appendChild(btn);
            container.appendChild(item);
        }

        async function apcLoadBrandCatalog() {
            try {
                const b = await getAdminCatalogBundle();
                apcBrandMetaByName = { ...b.brandMeta };
                apcCatalogBrands = [...b.catalogBrandNames];
            } catch (e) {
                console.error('Failed to load brands:', e);
                apcBrandMetaByName = {};
                apcCatalogBrands = [];
            }
            apcCatalogBrands.forEach((br) => getColorForTag(br));
            apcRenderBrandPicker();
            apcRenderBrandSelected();
        }

        function apcCertCatImageSrc(rec) {
            const name = tabbedCertApiName(rec);
            if (rec && rec.ref_id && rec.has_image) {
                return '/api/admin/reference/certifications/' + rec.ref_id + '/image?v=' + encodeURIComponent(String(rec.ref_id));
            }
            if (rec && rec.id && rec.has_image) {
                return '/api/admin/reference/certifications/' + rec.id + '/image?v=' + encodeURIComponent(String(rec.id));
            }
            const m = apcCertMetaByName[name];
            if (m && m.id && m.has_image) {
                return '/api/admin/reference/certifications/' + m.id + '/image?v=' + encodeURIComponent(String(m.id));
            }
            if (rec && rec.image_filename) return '/uploads/' + rec.image_filename;
            const CERT_IMAGE_MAP = UI ? UI.CERT_IMAGE_MAP : {};
            return CERT_IMAGE_MAP[name] || null;
        }

        function apcAddCertToProduct(rawName) {
            const value = String(rawName || '').trim();
            if (!value) return;
            if (apcTagContainers.certifications.some((x) => x.name === value)) return;
            getColorForTag(value);
            const cat = apcCatalogCertifications.find((r) => tabbedCertApiName(r) === value);
            const refId = cat && typeof cat.id === 'number' ? cat.id : null;
            const hasImg = !!(cat && cat.has_image);
            apcTagContainers.certifications.push({
                name: value,
                image_filename: null,
                file: null,
                replace_image: false,
                ref_id: refId,
                has_image: hasImg,
            });
            apcRenderCertPicker();
            apcRenderCertSelected();
        }

        function apcRenderCertPicker() {
            const container = E('contribute-certifications-picker');
            const searchEl = E('search-contribute-certifications');
            if (!container) return;
            const q = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            container.innerHTML = '';
            apcCatalogCertifications.forEach((rec) => {
                const value = tabbedCertApiName(rec);
                if (!value) return;
                if (apcTagContainers.certifications.some((x) => x.name === value)) return;
                if (q && !value.toLowerCase().includes(q)) return;
                const certImgSrc = apcCertCatImageSrc(typeof rec === 'object' ? rec : { name: value });
                const cell = document.createElement('div');
                cell.className =
                    'sidebar-filter-item' + (certImgSrc ? ' sidebar-filter-item--cert' : '');
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                cell.appendChild(cb);
                if (certImgSrc) {
                    const img = document.createElement('img');
                    img.className = 'sidebar-cert-picker-img sidebar-picker-ref-img';
                    img.src = certImgSrc;
                    img.alt = value;
                    cell.appendChild(img);
                } else {
                    const swatch = document.createElement('div');
                    swatch.className = 'tab-color';
                    swatch.style.backgroundColor = getColorForTag(value);
                    cell.appendChild(swatch);
                }
                const span = document.createElement('span');
                span.textContent = value;
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcAddCertToProduct(value);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderCertSelected() {
            const container = E('contribute-selected-certifications');
            if (!container) return;
            container.innerHTML = '';
            apcTagContainers.certifications.forEach((c, index) => {
                const block = document.createElement('div');
                block.className = 'admin-cert-selected-block';

                const item = document.createElement('div');
                item.className = 'sidebar-selected-chip';

                const certThumbSrc = apcCertCatImageSrc(c);
                const left = document.createElement('div');
                left.className =
                    'sidebar-chip-left' + (certThumbSrc ? ' sidebar-chip-left--brand' : '');

                if (certThumbSrc) {
                    const img = document.createElement('img');
                    img.className = 'sidebar-brand-chip-img sidebar-picker-ref-img';
                    img.src = certThumbSrc;
                    img.alt = c.name || '';
                    left.appendChild(img);
                } else {
                    const swatch = document.createElement('div');
                    swatch.className = 'sidebar-chip-color';
                    swatch.style.backgroundColor = getColorForTag(c.name);
                    left.appendChild(swatch);
                }
                const span = document.createElement('span');
                span.textContent = c.name;
                left.appendChild(span);

                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'sidebar-chip-remove';
                btn.setAttribute('aria-label', 'Remove ' + (c.name || 'certification'));
                btn.textContent = '✕';
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcTagContainers.certifications.splice(index, 1);
                    apcRenderCertPicker();
                    apcRenderCertSelected();
                });

                item.appendChild(left);
                item.appendChild(btn);

                block.appendChild(item);
                container.appendChild(block);
            });
        }

        function apcRefreshCertificationsUI() {
            apcRenderCertPicker();
            apcRenderCertSelected();
        }

        function apcAddTagToInput(containerKey, rawValue) {
            const value = String(rawValue || '').trim();
            if (!value) return;
            const list = apcTagContainers[containerKey];
            if (list.includes(value)) return;
            getColorForTag(value);
            list.push(value);
            const kind =
                containerKey === 'madeWithInput' ? 'made_with'
                : containerKey === 'madeWithoutInput' ? 'made_without'
                : 'attributes';
            apcRefreshColumn(kind);
        }

        async function apcAddNewAttributeFromField(kind) {
            const inputId =
                kind === 'made_with' ? 'contributeNewMadeWith'
                : kind === 'made_without' ? 'contributeNewMadeWithout'
                : 'contributeNewAttributes';
            const input = E(inputId);
            if (!input) return;
            const value = input.value.trim();
            if (!value) return;
            const path =
                kind === 'made_with' ? '/api/admin/reference/vocab/made-with'
                : kind === 'made_without' ? '/api/admin/reference/vocab/made-without'
                : '/api/admin/reference/vocab/features';
            try {
                const res = await fetch(path, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ name: value }),
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    apcShowValidationToast(data.detail || 'Could not add that value.');
                    return;
                }
                const canon = (data && data.name != null) ? String(data.name).trim() : value;
                if (!canon) return;
                apcEnsureInCatalog(kind, canon);
                getColorForTag(canon);
                const key =
                    kind === 'made_with' ? 'madeWithInput'
                    : kind === 'made_without' ? 'madeWithoutInput'
                    : 'attributesInput';
                apcAddTagToInput(key, canon);
                input.value = '';
                collapseContributePanelExpand(input.closest('[data-contribute-panel]'));
                invalidateAdminCatalogBundle();
                await apcLoadAttributeTags();
            } catch (err) {
                apcShowValidationToast((err && err.message) || 'Request failed.');
            }
        }

        async function apcLoadAttributeTags() {
            try {
                const b = await getAdminCatalogBundle();
                apcCatalogMadeWith = [...b.madeWith];
                apcCatalogMadeWithout = [...b.madeWithout];
                apcCatalogAttributes = [...b.attributes];
                if (UI) {
                    UI.primeTagColorsFromProducts([
                        { made_with: apcCatalogMadeWith, made_without: apcCatalogMadeWithout, attributes: apcCatalogAttributes },
                    ]);
                }
                apcRefreshAttributeColumns();
            } catch (e) {
                console.error('Failed to load attribute tags:', e);
            }
        }

        async function apcLoadExistingCerts() {
            try {
                const b = await getAdminCatalogBundle();
                apcCertMetaByName = { ...b.certMeta };
                apcCatalogCertifications = b.apcCatalogCertifications.map((x) => ({ ...x }));
                const names = apcCatalogCertifications.map((x) => x.name);
                if (UI) UI.primeTagColorsFromProducts([{ certifications: names }]);
                apcRefreshCertificationsUI();
            } catch (e) {
                console.error('Failed to load certifications:', e);
                apcCertMetaByName = {};
                apcCatalogCertifications = [];
            }
        }

        function apcSetMadeInCountry(value) {
            apcSelectedMadeIn = (value && String(value).trim()) || '';
            const hi = E('madeIn');
            if (hi) hi.value = apcSelectedMadeIn;
            apcRenderMadeInPicker();
            apcRenderMadeInSelected();
        }

        function apcRenderMadeInPicker() {
            const container = E('contribute-made-in-picker');
            const searchEl = E('search-contribute-made-in');
            if (!container || !UI) return;
            const q = searchEl && searchEl.value ? searchEl.value.toLowerCase().trim() : '';
            container.innerHTML = '';
            apcCatalogMadeInCountries.forEach((value) => {
                if (value === apcSelectedMadeIn) return;
                if (q && !value.toLowerCase().includes(q)) return;
                const cell = document.createElement('div');
                cell.className = 'sidebar-filter-item';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'tab-checkbox';
                const flag = document.createElement('img');
                flag.className = 'sidebar-country-flag';
                flag.src = UI.getFlagUrl(value);
                flag.alt = '';
                flag.title = value;
                flag.onerror = () => {
                    flag.style.display = 'none';
                };
                const span = document.createElement('span');
                span.textContent = value;
                cell.appendChild(cb);
                cell.appendChild(flag);
                cell.appendChild(span);
                cell.addEventListener('click', (e) => {
                    e.stopPropagation();
                    apcSetMadeInCountry(value);
                });
                container.appendChild(cell);
            });
        }

        function apcRenderMadeInSelected() {
            const container = E('contribute-selected-made-in');
            if (!container || !UI) return;
            container.innerHTML = '';
            if (!apcSelectedMadeIn) return;
            const item = document.createElement('div');
            item.className = 'sidebar-selected-chip';
            const left = document.createElement('div');
            left.className = 'sidebar-chip-left';
            const flag = document.createElement('img');
            flag.className = 'sidebar-country-flag';
            flag.src = UI.getFlagUrl(apcSelectedMadeIn);
            flag.alt = '';
            flag.title = apcSelectedMadeIn;
            flag.onerror = () => {
                flag.style.display = 'none';
            };
            const span = document.createElement('span');
            span.textContent = apcSelectedMadeIn;
            left.appendChild(flag);
            left.appendChild(span);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sidebar-chip-remove';
            btn.textContent = '✕';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                apcSetMadeInCountry('');
            });
            item.appendChild(left);
            item.appendChild(btn);
            container.appendChild(item);
        }

        async function apcLoadMadeInCatalog() {
            try {
                const b = await getAdminCatalogBundle();
                apcCatalogMadeInCountries = [...b.madeInCountries];
            } catch (err) {
                console.error('Failed to load countries:', err);
                apcCatalogMadeInCountries = Array.from(new Set(APC_DEFAULT_MADE_IN)).sort((a, b) =>
                    a.localeCompare(b, undefined, { sensitivity: 'base' })
                );
            }
            apcRenderMadeInPicker();
            apcRenderMadeInSelected();
        }

        function apcPreventEnterSubmit(el) {
            if (!el) return;
            el.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') e.preventDefault();
            });
        }

        function apcShowAlert(message, isError) {
            const alertsDiv = document.getElementById(alertsId);
            if (!alertsDiv) return;
            alertsDiv.innerHTML = '';
            const alert = document.createElement('div');
            alert.className = isError ? 'alert alert-error' : 'alert alert-success';
            alert.textContent = message;
            alertsDiv.appendChild(alert);
            if (!isError) {
                setTimeout(() => {
                    alertsDiv.innerHTML = '';
                }, 4000);
            }
        }

        function apcShowValidationToast(message) {
            const msg = String(message || '').trim();
            if (!msg) return;
            if (typeof window.tabbedShowGlobalToast === 'function') {
                window.tabbedShowGlobalToast(msg, { isError: true });
            } else {
                apcShowAlert(msg, true);
            }
        }

        function apcResetInternals() {
            apcTagContainers.madeWithInput = [];
            apcTagContainers.madeWithoutInput = [];
            apcTagContainers.attributesInput = [];
            apcTagContainers.certifications = [];
            apcSetMadeInCountry('');
            apcSetCategory('');
            apcSetBrand('');
            apcLoadAttributeTags();
            apcLoadMadeInCatalog();
            apcLoadExistingCerts();
            apcLoadCategoryCatalog();
            apcLoadBrandCatalog();
            collapseAllContributePanelExpands();
            const hi = E('productImageName');
            if (hi) hi.textContent = '';
            const pi = E('productImage');
            if (pi) pi.value = '';
            revokeApcProductNewPreview();
        }

        function apcPopulateFromProduct(p) {
            if (!p) return;
            const pn = E('productName');
            if (pn) pn.value = p.name || '';
            const pr = E('price');
            if (pr) pr.value = p.price != null && p.price !== '' ? String(p.price) : '';
            const desc = E('productDescription');
            if (desc) desc.value = p.description || '';
            const link = E('productLink');
            if (link) link.value = p.product_link || '';
            const ec = E('earnsCommission');
            if (ec) ec.checked = !!p.earns_commission;
            const iv = E('isVerified');
            if (iv) iv.checked = !!p.is_verified;

            const subVal = (p.subcategory != null ? String(p.subcategory) : '').trim();
            apcSelectedCategory = String(p.main_category || p.category || '').trim();
            const mEl = E('mainCategory');
            if (mEl) mEl.value = apcSelectedCategory;
            apcRenderCategoryPicker();
            apcRenderCategorySelected();
            apcRefreshSubcategorySelect(subVal);
            apcSetBrand(p.brand_name || '');
            apcSetMadeInCountry(p.made_in || '');

            apcTagContainers.madeWithInput = Array.isArray(p.made_with) ? [...p.made_with] : [];
            apcTagContainers.madeWithoutInput = Array.isArray(p.made_without) ? [...p.made_without] : [];
            apcTagContainers.attributesInput = Array.isArray(p.attributes) ? [...p.attributes] : [];
            apcTagContainers.madeWithInput.forEach((t) => apcEnsureInCatalog('made_with', t));
            apcTagContainers.madeWithoutInput.forEach((t) => apcEnsureInCatalog('made_without', t));
            apcTagContainers.attributesInput.forEach((t) => apcEnsureInCatalog('attributes', t));
            apcRefreshAttributeColumns();

            apcTagContainers.certifications = [];
            (p.certifications || []).forEach((raw) => {
                if (typeof raw === 'string') {
                    const meta = apcCertMetaByName[raw];
                    apcTagContainers.certifications.push({
                        name: raw,
                        image_filename: null,
                        file: null,
                        replace_image: false,
                        ref_id: meta ? meta.id : null,
                        has_image: meta ? meta.has_image : false,
                    });
                } else if (raw && typeof raw === 'object') {
                    const nm = String(raw.name || '').trim();
                    const meta = apcCertMetaByName[nm];
                    const rid = raw.id != null ? raw.id : (meta ? meta.id : null);
                    apcTagContainers.certifications.push({
                        name: nm,
                        image_filename: raw.image_filename || null,
                        file: null,
                        replace_image: false,
                        ref_id: rid,
                        has_image: meta ? meta.has_image : !!raw.image_url,
                    });
                }
            });
            apcRefreshCertificationsUI();

            const ph = E('productImageName');
            if (ph) {
                ph.textContent = p.product_image_filename
                    ? `Current: ${p.product_image_filename} (choose file to replace)`
                    : 'No product image';
            }
            const pi = E('productImage');
            if (pi) pi.value = '';
            revokeApcProductNewPreview();
            collapseAllContributePanelExpands();
            if (sheetMode) delete formEl.dataset.dirty;
        }

        initContributePanelHeaders();

        function bindEnter(id, fn) {
            const el = E(id);
            if (!el) return;
            el.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    fn();
                }
            });
        }
        bindEnter('contributeNewMadeWith', () => apcAddNewAttributeFromField('made_with'));
        bindEnter('contributeNewMadeWithout', () => apcAddNewAttributeFromField('made_without'));
        bindEnter('contributeNewAttributes', () => apcAddNewAttributeFromField('attributes'));

        apcPreventEnterSubmit(E('search-contribute-made-with'));
        apcPreventEnterSubmit(E('search-contribute-made-without'));
        apcPreventEnterSubmit(E('search-contribute-attributes'));
        apcPreventEnterSubmit(E('search-contribute-made-in'));
        apcPreventEnterSubmit(E('search-contribute-certifications'));
        apcPreventEnterSubmit(E('search-contribute-category'));
        apcPreventEnterSubmit(E('search-contribute-subcategory'));
        apcPreventEnterSubmit(E('search-contribute-brand'));

        const smw = E('search-contribute-made-with');
        if (smw) smw.addEventListener('input', () => apcRenderPicker('made_with'));
        const smo = E('search-contribute-made-without');
        if (smo) smo.addEventListener('input', () => apcRenderPicker('made_without'));
        const sat = E('search-contribute-attributes');
        if (sat) sat.addEventListener('input', () => apcRenderPicker('attributes'));
        const smi = E('search-contribute-made-in');
        if (smi) smi.addEventListener('input', () => apcRenderMadeInPicker());
        const sc = E('search-contribute-certifications');
        if (sc) sc.addEventListener('input', () => apcRenderCertPicker());
        const sct = E('search-contribute-category');
        if (sct) sct.addEventListener('input', () => apcRenderCategoryPicker());
        const ssub = E('search-contribute-subcategory');
        if (ssub) ssub.addEventListener('input', () => apcRenderSubcategoryPicker());
        const sb = E('search-contribute-brand');
        if (sb) sb.addEventListener('input', () => apcRenderBrandPicker());

        const pImg = E('productImage');
        if (pImg) {
            pImg.addEventListener('change', (e) => {
                const file = e.target.files && e.target.files[0];
                const name = file?.name || '';
                const hint = E('productImageName');
                revokeApcProductNewPreview();

                if (hint) {
                    if (mode === 'edit') {
                        hint.textContent = file ? `Replace with: ${name}` : '';
                    } else {
                        hint.textContent = name || '';
                    }
                }

                if (!file) return;

                const nw = E('productNewImagePreviewWrap');
                const ni = E('productNewImagePreview');
                apcProductNewImageObjectUrl = URL.createObjectURL(file);
                if (nw && ni) {
                    ni.src = apcProductNewImageObjectUrl;
                    ni.alt = mode === 'edit' ? 'New image preview' : 'Image preview';
                    nw.hidden = false;
                }
            });
        }

        if (mode === 'add' || sheetMode) {
            const aiBtn = E('productLinkAiBtn');
            if (aiBtn) {
                aiBtn.addEventListener('click', async () => {
                    const linkEl = E('productLink');
                    const raw = linkEl && linkEl.value ? linkEl.value.trim() : '';
                    if (!raw) {
                        apcShowValidationToast('Enter a product page URL first.');
                        return;
                    }
                    try {
                        new URL(String(raw));
                    } catch (e) {
                        apcShowValidationToast('Please enter a valid http(s) URL.');
                        return;
                    }
                    aiBtn.disabled = true;
                    const prev = aiBtn.textContent;
                    aiBtn.textContent = 'Working…';
                    try {
                        const res = await fetch('/api/admin/products/ai-populate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            credentials: 'same-origin',
                            body: JSON.stringify({ url: raw }),
                        });
                        const rawText = await res.text();
                        let data = {};
                        try {
                            data = rawText ? JSON.parse(rawText) : {};
                        } catch (e) {
                            apcShowAlert(
                                res.status === 503
                                    ? 'AI populate unavailable (server configuration).'
                                    : rawText.slice(0, 500) || 'AI populate failed.',
                                true
                            );
                            return;
                        }
                        if (!res.ok) {
                            let msg =
                                res.status === 503
                                    ? 'AI is not configured (ANTHROPIC_API_KEY on the server) or the service is unavailable.'
                                    : 'AI populate failed.';
                            const d = data && data.detail;
                            if (typeof d === 'string' && d) msg = d;
                            else if (Array.isArray(d) && d.length) {
                                msg = d
                                    .map((x) => (x && x.msg ? x.msg : String(x)))
                                    .join(' ');
                            }
                            apcShowAlert(msg, true);
                            return;
                        }
                        const p = data.product;
                        if (!p) {
                            apcShowAlert('No product data returned.', true);
                            return;
                        }
                        const bn = (p.brand_name || '').trim();
                        if (bn && apcCatalogBrands.indexOf(bn) === -1) {
                            apcCatalogBrands.push(bn);
                            apcCatalogBrands.sort((a, b) =>
                                a.localeCompare(b, undefined, { sensitivity: 'base' })
                            );
                        }
                        apcPopulateFromProduct(p);
                        if (sheetMode) formEl.dataset.dirty = '1';
                        const lines = (data.messages || []).filter(Boolean);
                        if (lines.length) {
                            if (typeof window.tabbedShowGlobalToast === 'function') {
                                window.tabbedShowGlobalToast(lines.join(' '));
                            } else {
                                apcShowAlert(lines.join(' '), false);
                            }
                        } else {
                            apcShowAlert('', false);
                        }
                    } catch (err) {
                        apcShowAlert(err.message || 'Request failed', true);
                    } finally {
                        aiBtn.disabled = false;
                        aiBtn.textContent = prev;
                    }
                });
            }

            if (!sheetMode && mode === 'add') {
                window.__tabbedAdminPreFillAddFromProduct = (data) => {
                    if (!data) return;
                    const pid = data.id;
                    const bn = (data.brand_name || '').trim();
                    if (bn && apcCatalogBrands.indexOf(bn) === -1) {
                        apcCatalogBrands.push(bn);
                        apcCatalogBrands.sort((a, b) =>
                            a.localeCompare(b, undefined, { sensitivity: 'base' })
                        );
                    }
                    apcPopulateFromProduct(data);
                    const imHint = E('productImageName');
                    if (imHint) {
                        const had = data.product_image_filename
                            ? ' Source file was: ' + data.product_image_filename + '.'
                            : '';
                        imHint.textContent = 'Image not copied—upload a new file or leave empty.' + had;
                    }
                    if (typeof window.tabbedShowGlobalToast === 'function') {
                        window.tabbedShowGlobalToast('Form filled from product #' + pid + '.');
                    } else {
                        apcShowAlert('Form filled from product #' + pid + '.', false);
                    }
                };
            }
        }

        let apcSubmitInFlight = false;

        function apcValidateAndBuildFormData() {
            const productNameVal =
                E('productName') && E('productName').value ? E('productName').value.trim() : '';
            if (!productNameVal) {
                return { ok: false, msg: 'Please enter a product name.' };
            }
            const madeInVal = E('madeIn') && E('madeIn').value ? E('madeIn').value.trim() : '';
            if (!madeInVal) {
                return { ok: false, msg: 'Please select a country under Made In.' };
            }
            const categoryVal =
                E('mainCategory') && E('mainCategory').value ? E('mainCategory').value.trim() : '';
            if (!categoryVal) {
                return { ok: false, msg: 'Please select a main category.' };
            }
            const brandVal = E('brandName') && E('brandName').value ? E('brandName').value.trim() : '';
            if (!brandVal) {
                return { ok: false, msg: 'Please choose a brand from the list.' };
            }

            const certMeta = apcTagContainers.certifications.map((c) => ({
                id: c.ref_id != null ? c.ref_id : null,
                name: c.name,
                image_filename: c.image_filename || null,
                replace_image: !!(c.file),
            }));

            const formData = new FormData();
            formData.append('product_name', E('productName').value);
            formData.append('brand_name', brandVal);
            formData.append('main_category', categoryVal);
            const subEl = E('subcategory');
            formData.append('subcategory', subEl && subEl.value ? subEl.value.trim() : '');
            formData.append('made_in', madeInVal);
            const priceEl = E('price');
            formData.append('price', priceEl && priceEl.value ? priceEl.value : '');
            formData.append('product_link', E('productLink').value || '');
            if (E('earnsCommission') && E('earnsCommission').checked) {
                formData.append('earns_commission', '1');
            }
            if (E('isVerified') && E('isVerified').checked) {
                formData.append('is_verified', '1');
            }
            formData.append('description', E('productDescription').value.trim());
            formData.append('made_with', JSON.stringify(apcTagContainers.madeWithInput));
            formData.append('made_without', JSON.stringify(apcTagContainers.madeWithoutInput));
            formData.append('attributes', JSON.stringify(apcTagContainers.attributesInput));
            formData.append('certifications', JSON.stringify(certMeta));
            apcTagContainers.certifications.forEach((c) => {
                if (c.file) formData.append('cert_images', c.file);
            });
            const bIm = E('brandImage');
            if (bIm && bIm.files[0]) formData.append('brand_image', bIm.files[0]);
            const pIm = E('productImage');
            if (pIm && pIm.files[0]) formData.append('product_image', pIm.files[0]);

            let url = '/api/admin/products/add';
            if (mode === 'edit') {
                const sel = document.getElementById('editProductSelect');
                const pid = sel && sel.value;
                if (!pid) {
                    return { ok: false, msg: 'Select a product to edit.' };
                }
                url = '/api/admin/products/' + encodeURIComponent(pid);
            }
            if (sheetMode && getSheetProductId) {
                const pid = getSheetProductId();
                url = pid
                    ? '/api/admin/products/' + encodeURIComponent(pid)
                    : '/api/admin/products/add';
            }
            return { ok: true, formData, url };
        }

        formEl.tabbedAdminPopulate = apcPopulateFromProduct;
        if (sheetMode) {
            const markDirty = () => {
                formEl.dataset.dirty = '1';
            };
            const rowRoot = formEl.closest('tr[data-admin-product-row]');
            const dirtyTarget = rowRoot || formEl;
            dirtyTarget.addEventListener('input', markDirty, true);
            dirtyTarget.addEventListener('change', markDirty, true);
        }
        formEl.tabbedAdminSave = async function () {
            if (apcSubmitInFlight) return { ok: false, error: 'busy' };
            apcSubmitInFlight = true;
            try {
                const v = apcValidateAndBuildFormData();
                if (!v.ok) {
                    apcShowValidationToast(v.msg);
                    return { ok: false, error: v.msg };
                }
                const response = await fetch(v.url, {
                    method: 'POST',
                    body: v.formData,
                    credentials: 'same-origin',
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok) {
                    apcShowAlert('', false);
                    delete formEl.dataset.dirty;
                    if (typeof window.tabbedShowGlobalToast === 'function') {
                        window.tabbedShowGlobalToast(sheetMode ? 'Product saved.' : (mode === 'edit' ? 'Product updated.' : 'Product added.'));
                    }
                    if (!sheetMode) {
                        if (mode === 'add') {
                            formEl.reset();
                            const nm = E('productImageName');
                            if (nm) nm.textContent = '';
                            apcResetInternals();
                        } else if (typeof window.__adminReloadProductList === 'function') {
                            await window.__adminReloadProductList();
                        }
                        const backdropId =
                            mode === 'edit' ? 'adminModalEditProductBackdrop' : 'adminModalAddProductBackdrop';
                        if (typeof window.adminSetModalOpen === 'function') {
                            window.adminSetModalOpen(backdropId, false);
                        }
                    }
                    return { ok: true, data };
                }
                const detail = data.detail;
                const msg =
                    typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : 'Request failed';
                apcShowAlert(msg, true);
                return { ok: false, error: msg, data };
            } catch (err) {
                apcShowAlert(err.message || 'Request failed', true);
                return { ok: false, error: err.message };
            } finally {
                apcSubmitInFlight = false;
            }
        };

        formEl.addEventListener('reset', () => {
            setTimeout(() => {
                apcResetInternals();
            }, 0);
        });

        formEl.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (sheetMode) return;
            if (apcSubmitInFlight) return;
            apcSubmitInFlight = true;
            const submitBtn = formEl.querySelector('button[type="submit"]');
            const prevLabel = submitBtn ? submitBtn.textContent : '';
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = mode === 'edit' ? 'Saving…' : 'Adding…';
            }

            try {
                const v = apcValidateAndBuildFormData();
                if (!v.ok) {
                    apcShowValidationToast(v.msg);
                    return;
                }
                const response = await fetch(v.url, {
                    method: 'POST',
                    body: v.formData,
                    credentials: 'same-origin',
                });
                const data = await response.json().catch(() => ({}));

                if (response.ok) {
                    apcShowAlert('', false);
                    const toastMsg = mode === 'edit' ? 'Product updated.' : 'Product added.';
                    if (mode === 'add') {
                        formEl.reset();
                        const nm = E('productImageName');
                        if (nm) nm.textContent = '';
                        apcResetInternals();
                    } else if (typeof window.__adminReloadProductList === 'function') {
                        await window.__adminReloadProductList();
                    }
                    const backdropId =
                        mode === 'edit' ? 'adminModalEditProductBackdrop' : 'adminModalAddProductBackdrop';
                    if (typeof window.adminSetModalOpen === 'function') {
                        window.adminSetModalOpen(backdropId, false);
                    }
                    if (typeof window.tabbedShowGlobalToast === 'function') {
                        window.tabbedShowGlobalToast(toastMsg);
                    }
                } else {
                    const detail = data.detail;
                    const msg =
                        typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : 'Request failed';
                    apcShowAlert(msg, true);
                }
            } catch (err) {
                apcShowAlert(err.message || 'Request failed', true);
            } finally {
                apcSubmitInFlight = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = prevLabel;
                }
            }
        });

        apcLoadAttributeTags();
        apcLoadMadeInCatalog();
        apcLoadExistingCerts();
        apcLoadCategoryCatalog();
        apcLoadBrandCatalog();

        if (mode === 'edit' && !sheetMode) {
            window.__tabbedAdminPopulateEdit = apcPopulateFromProduct;
            window.__tabbedAdminResetEdit = () => {
                apcResetInternals();
            };
        }

        if (!sheetMode) {
            window.__tabbedAdminCategoryReloadFns = window.__tabbedAdminCategoryReloadFns || [];
            window.__tabbedAdminCategoryReloadFns.push(() => apcLoadCategoryCatalog());
        }

        const myReferenceRefresh = async function () {
            await Promise.all([
                apcLoadCategoryCatalog(),
                apcLoadBrandCatalog(),
                apcLoadExistingCerts(),
                apcLoadAttributeTags(),
                apcLoadMadeInCatalog(),
            ]);
        };
        if (!sheetMode) {
            window.__tabbedAdminReferenceRefreshFns = window.__tabbedAdminReferenceRefreshFns || [];
            window.__tabbedAdminReferenceRefreshFns.push(myReferenceRefresh);
        }
        adminRefreshReferencePickers = myReferenceRefresh;
    }

    window.tabbedBootAdminProductForm = bootAdminForm;

    bootAdminForm('ac-', { mode: 'add', formId: 'adminAddProductForm', alertsId: 'admin-add-alerts' });
    bootAdminForm('ae-', { mode: 'edit', formId: 'adminEditProductForm', alertsId: 'admin-edit-alerts' });

    window.__tabbedAdminRefreshReferencePickers = async function () {
        invalidateAdminCatalogBundle();
        const fns = window.__tabbedAdminReferenceRefreshFns || [];
        if (!fns.length) {
            return adminRefreshReferencePickers ? adminRefreshReferencePickers() : Promise.resolve();
        }
        await Promise.all(fns.map((fn) => fn()));
    };
})();
