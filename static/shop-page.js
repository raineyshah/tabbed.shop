/**
 * Category shop: admin-style filter panels + product grid.
 */
(function () {
    const slugRaw =
        typeof window.__TABBED_CATEGORY_SLUG__ === 'string' ? window.__TABBED_CATEGORY_SLUG__.trim() : '';
    const slugLower = slugRaw.toLowerCase();
    const searchQuery =
        typeof window.__TABBED_SEARCH_QUERY__ === 'string' ? window.__TABBED_SEARCH_QUERY__.trim() : '';
    const searchMode = Boolean(searchQuery);
    if (!slugRaw && !searchMode) return;
    if (slugRaw && searchMode) {
        console.warn('Both category slug and search query set; using search.');
    }

    const UI = window.TabbedCatalogUI;
    if (!UI) {
        console.error('TabbedCatalogUI missing; load catalog-ui.js first.');
        return;
    }

    const CERT_IMAGE_MAP = UI.CERT_IMAGE_MAP || {};
    const sectionsMount = document.getElementById('shop-sidebar-sections');
    const grid = document.getElementById('products-container');
    const emptyEl = document.getElementById('shop-empty-state');

    const FACETS = [
        { key: 'brands', label: 'Brand' },
        { key: 'certifications', label: 'Certifications' },
        { key: 'made_in', label: 'Made In' },
        { key: 'made_with', label: 'Made With' },
        { key: 'made_without', label: 'Made Without' },
        { key: 'attributes', label: 'Features' },
    ];

    /** @type {Record<string, string[]>} most recently toggled first */
    const selectedOrder = {};
    FACETS.forEach((f) => {
        selectedOrder[f.key] = [];
    });

    let allProducts = [];
    let brandLogos = {};
    /** Facet certification name → image URL (from API); static map fills legacy names */
    let certificationImages = {};

    function facetBrandLogoSrc(logoVal) {
        if (!logoVal) return '';
        const s = String(logoVal);
        if (s.startsWith('/api/')) return s;
        return '/uploads/' + s;
    }

    function certificationFacetImgSrc(name) {
        const n = name == null ? '' : String(name);
        if (!n) return '';
        if (certificationImages[n]) return certificationImages[n];
        return CERT_IMAGE_MAP[n] || '';
    }
    let favoriteIds = new Set();

    /** Fixed page size (no UI); keep in sync with grid slicing below. */
    const SHOP_GRID_PAGE_SIZE = 100;
    /** @type {'default' | 'asc' | 'desc'} */
    let priceSortOrder = 'default';
    let shopGridPage = 1;

    const FILTER_MODE_STORAGE_KEY = 'tabbed_shop_filter_mode';
    /** @type {'and' | 'or'} */
    let filterMode = 'and';

    function loadPersistedFilterMode() {
        try {
            const raw = window.localStorage && window.localStorage.getItem(FILTER_MODE_STORAGE_KEY);
            if (raw === 'or' || raw === 'and') filterMode = raw;
        } catch (e) {
            /* ignore */
        }
    }

    function persistFilterMode() {
        try {
            if (window.localStorage) {
                window.localStorage.setItem(FILTER_MODE_STORAGE_KEY, filterMode);
            }
        } catch (e) {
            /* ignore */
        }
    }

    function totalFacetSelections() {
        return FACETS.reduce((n, f) => n + selectedOrder[f.key].length, 0);
    }

    function isFavorite(id) {
        return favoriteIds.has(Number(id));
    }

    async function refreshFavorites() {
        try {
            const res = await fetch('/api/favorites', { cache: 'no-store' });
            const data = res.ok ? await res.json() : { ids: [] };
            const ids = Array.isArray(data.ids) ? data.ids : [];
            favoriteIds = new Set(ids.map(Number));
        } catch (e) {
            favoriteIds = new Set();
        }
    }

    const FAVORITE_SIGN_IN_TOAST = 'Please Sign In to save items as favorites';

    function showFavoriteSignInToast() {
        if (typeof window.tabbedShowGlobalToast === 'function') {
            window.tabbedShowGlobalToast(FAVORITE_SIGN_IN_TOAST, { isError: false });
        }
    }

    function isUserSignedInForFavorites() {
        const getUser = window.TabbedGetContributorCookie;
        return typeof getUser === 'function' && !!getUser();
    }

    async function toggleFavorite(productId) {
        if (!isUserSignedInForFavorites()) {
            showFavoriteSignInToast();
            return;
        }
        const id = Number(productId);
        const next = !favoriteIds.has(id);
        try {
            const res = await fetch('/api/favorites', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ product_id: id, favorited: next }),
            });
            if (!res.ok) {
                if (res.status === 401) {
                    showFavoriteSignInToast();
                }
                return;
            }
            if (next) favoriteIds.add(id);
            else favoriteIds.delete(id);
            renderGrid();
        } catch (e) {
            /* ignore */
        }
    }

    /** AND mode: product must satisfy every facet that has selections. Within brand/made_in (single-valued), any selected value matches; certifications and tag facets require every selected value. */
    function productMatchesAnd(p) {
        const brands = selectedOrder.brands;
        if (brands.length && !brands.includes(p.brand_name)) return false;

        const madeIn = selectedOrder.made_in;
        if (madeIn.length && !madeIn.includes(p.made_in)) return false;

        if (selectedOrder.certifications.length) {
            const certNames = (p.certifications || []).map((x) => UI.tabbedCertName(x));
            const pcs = new Set(certNames);
            if (!selectedOrder.certifications.every((c) => pcs.has(c))) return false;
        }

        const needAll = (key, getter) => {
            const arr = selectedOrder[key];
            if (!arr.length) return true;
            const pset = new Set(getter() || []);
            for (const x of arr) {
                if (!pset.has(x)) return false;
            }
            return true;
        };

        if (!needAll('made_with', () => p.made_with)) return false;
        if (!needAll('made_without', () => p.made_without)) return false;
        if (!needAll('attributes', () => p.attributes)) return false;

        return true;
    }

    /** OR across every selected value: product matches if it satisfies at least one selected filter. */
    function productMatchesOr(p) {
        if (!totalFacetSelections()) return true;
        const certs = new Set((p.certifications || []).map((x) => UI.tabbedCertName(x)));
        const withSet = new Set(p.made_with || []);
        const withoutSet = new Set(p.made_without || []);
        const attrSet = new Set(p.attributes || []);

        if (selectedOrder.brands.some((b) => p.brand_name === b)) return true;
        if (selectedOrder.made_in.some((m) => p.made_in === m)) return true;
        if (selectedOrder.certifications.some((c) => certs.has(c))) return true;
        if (selectedOrder.made_with.some((t) => withSet.has(t))) return true;
        if (selectedOrder.made_without.some((t) => withoutSet.has(t))) return true;
        if (selectedOrder.attributes.some((a) => attrSet.has(a))) return true;

        return false;
    }

    function filteredProducts() {
        const fn = filterMode === 'or' ? productMatchesOr : productMatchesAnd;
        return allProducts.filter(fn);
    }

    function productPriceNum(p) {
        const n = Number(p && p.price);
        return Number.isFinite(n) ? n : 0;
    }

    function sortedFilteredProducts() {
        const list = filteredProducts();
        if (priceSortOrder === 'default') return list;
        const dir = priceSortOrder === 'asc' ? 1 : -1;
        return list.slice().sort((a, b) => {
            const da = productPriceNum(a);
            const db = productPriceNum(b);
            if (da !== db) return (da - db) * dir;
            return String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' });
        });
    }

    function updateShopGridFooter(total, pages) {
        const footer = document.getElementById('shop-grid-footer');
        const sortEl = document.getElementById('shop-grid-sort');
        const labelEl = document.getElementById('shop-grid-page-label');
        const prevEl = document.getElementById('shop-grid-prev');
        const nextEl = document.getElementById('shop-grid-next');
        if (!footer) return;
        footer.hidden = false;
        if (sortEl) sortEl.value = priceSortOrder;
        if (labelEl) {
            labelEl.textContent = total === 0 ? '—' : shopGridPage + ' / ' + pages;
        }
        if (prevEl) prevEl.disabled = total === 0 || shopGridPage <= 1;
        if (nextEl) nextEl.disabled = total === 0 || shopGridPage >= pages;
    }

    function bindShopGridFooter() {
        const sortEl = document.getElementById('shop-grid-sort');
        const prevEl = document.getElementById('shop-grid-prev');
        const nextEl = document.getElementById('shop-grid-next');
        if (sortEl && !sortEl._tabbedShopFooterBound) {
            sortEl._tabbedShopFooterBound = true;
            sortEl.addEventListener('change', () => {
                priceSortOrder =
                    sortEl.value === 'asc' || sortEl.value === 'desc' ? sortEl.value : 'default';
                shopGridPage = 1;
                renderGrid();
            });
        }
        if (prevEl && !prevEl._tabbedShopFooterBound) {
            prevEl._tabbedShopFooterBound = true;
            prevEl.addEventListener('click', () => {
                shopGridPage = Math.max(1, shopGridPage - 1);
                renderGrid();
            });
        }
        if (nextEl && !nextEl._tabbedShopFooterBound) {
            nextEl._tabbedShopFooterBound = true;
            nextEl.addEventListener('click', () => {
                shopGridPage += 1;
                renderGrid();
            });
        }
    }

    function renderGrid() {
        if (!grid) return;
        const list = sortedFilteredProducts();
        const total = list.length;
        const pages = Math.max(1, Math.ceil(total / SHOP_GRID_PAGE_SIZE));
        if (shopGridPage > pages) shopGridPage = pages;
        if (shopGridPage < 1) shopGridPage = 1;
        const start = (shopGridPage - 1) * SHOP_GRID_PAGE_SIZE;
        const slice = list.slice(start, start + SHOP_GRID_PAGE_SIZE);

        grid.innerHTML = '';
        if (emptyEl) {
            emptyEl.hidden = total > 0;
        }
        const handlers = {
            isFavorite,
            onToggleFavorite: toggleFavorite,
            onOpenDetail: (p) =>
                UI.openProductModal(p, {
                    isFavorite,
                    onToggleFavorite: toggleFavorite,
                }),
        };
        slice.forEach((p) => grid.appendChild(UI.createProductCard(p, handlers)));
        updateShopGridFooter(total, pages);
    }

    function resetAllShopFilters() {
        shopGridPage = 1;
        FACETS.forEach((f) => {
            selectedOrder[f.key].length = 0;
        });
        if (sectionsMount) {
            sectionsMount.querySelectorAll('[data-shop-search]').forEach((input) => {
                input.value = '';
            });
        }
        FACETS.forEach((f) => {
            paintFacetList(f.key);
            renderSelectedChips(f.key);
        });
        renderGrid();
    }

    function bindShopSidebarResetButton() {
        const btn = document.getElementById('shop-sidebar-reset-filters');
        if (!btn || btn._tabbedShopResetBound) return;
        btn._tabbedShopResetBound = true;
        btn.addEventListener('click', () => resetAllShopFilters());
    }

    try {
        window.TabbedResetShopFilters = resetAllShopFilters;
    } catch (e) {
        /* ignore */
    }

    function toggleSelection(facetKey, value) {
        shopGridPage = 1;
        const arr = selectedOrder[facetKey];
        const i = arr.indexOf(value);
        if (i >= 0) {
            arr.splice(i, 1);
        } else {
            arr.unshift(value);
        }
        paintFacetList(facetKey);
        renderSelectedChips(facetKey);
        renderGrid();
    }

    function buildPickerRow(facetKey, value) {
        const row = document.createElement('div');
        row.className = 'sidebar-filter-item';
        row.setAttribute('data-value', value);

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'tab-checkbox';
        cb.checked = false;
        cb.tabIndex = -1;

        if (facetKey === 'brands') {
            row.classList.add('sidebar-filter-item--brand');
            const logoFn = brandLogos[value];
            if (logoFn) {
                const img = document.createElement('img');
                img.className = 'sidebar-brand-logo';
                img.src = facetBrandLogoSrc(logoFn);
                img.alt = '';
                row.appendChild(cb);
                row.appendChild(img);
            } else {
                const swatch = document.createElement('div');
                swatch.className = 'tab-color';
                const color = UI.getColorForTag(value);
                swatch.style.backgroundColor = color;
                row.appendChild(cb);
                row.appendChild(swatch);
            }
        } else if (facetKey === 'certifications') {
            const imgSrc = certificationFacetImgSrc(value);
            if (imgSrc) {
                const sw = document.createElement('div');
                sw.className = 'sidebar-cert-swatch';
                sw.title = value;
                const im = document.createElement('img');
                im.className = 'sidebar-cert-icon';
                im.src = imgSrc;
                im.alt = value;
                im.title = value;
                sw.appendChild(im);
                row.appendChild(cb);
                row.appendChild(sw);
            } else {
                const swatch = document.createElement('div');
                swatch.className = 'tab-color';
                const color = UI.getColorForTag(value);
                swatch.style.backgroundColor = color;
                row.appendChild(cb);
                row.appendChild(swatch);
            }
        } else if (facetKey === 'made_in') {
            row.appendChild(cb);
            const flag = document.createElement('img');
            flag.className = 'sidebar-country-flag';
            flag.src = UI.getFlagUrl(value);
            flag.alt = '';
            flag.loading = 'lazy';
            flag.onerror = function () {
                this.style.visibility = 'hidden';
            };
            row.appendChild(flag);
        } else {
            const swatch = document.createElement('div');
            swatch.className = 'tab-color';
            const color = UI.getColorForTag(value);
            swatch.style.backgroundColor = color;
            row.appendChild(cb);
            row.appendChild(swatch);
        }

        const span = document.createElement('span');
        span.textContent = value;
        row.appendChild(span);

        row.addEventListener('click', (e) => {
            e.preventDefault();
            toggleSelection(facetKey, value);
        });
        return row;
    }

    function buildChipLeft(facetKey, value) {
        const left = document.createElement('div');
        left.className = 'sidebar-chip-left';

        if (facetKey === 'brands') {
            const logoFn = brandLogos[value];
            if (logoFn) {
                left.classList.add('sidebar-chip-left--brand');
                const img = document.createElement('img');
                img.className = 'sidebar-brand-chip-img';
                img.src = facetBrandLogoSrc(logoFn);
                img.alt = '';
                left.appendChild(img);
            } else {
                const swatch = document.createElement('div');
                swatch.className = 'sidebar-chip-color';
                const color = UI.getColorForTag(value);
                swatch.style.backgroundColor = color;
                left.appendChild(swatch);
            }
        } else if (facetKey === 'certifications') {
            const imgSrc = certificationFacetImgSrc(value);
            if (imgSrc) {
                const img = document.createElement('img');
                img.className = 'sidebar-chip-cert-icon';
                img.src = imgSrc;
                img.alt = value;
                img.title = value;
                left.appendChild(img);
            } else {
                const swatch = document.createElement('div');
                swatch.className = 'sidebar-chip-color';
                const color = UI.getColorForTag(value);
                swatch.style.backgroundColor = color;
                left.appendChild(swatch);
            }
        } else if (facetKey === 'made_in') {
            const flag = document.createElement('img');
            flag.className = 'sidebar-country-flag';
            flag.src = UI.getFlagUrl(value);
            flag.alt = '';
            flag.onerror = function () {
                this.style.display = 'none';
            };
            left.appendChild(flag);
        } else {
            const swatch = document.createElement('div');
            swatch.className = 'sidebar-chip-color';
            const color = UI.getColorForTag(value);
            swatch.style.backgroundColor = color;
            left.appendChild(swatch);
        }

        const span = document.createElement('span');
        span.textContent = value;
        left.appendChild(span);
        return left;
    }

    /** Renders under this facet’s header, above search + list; stays visible when the panel body is collapsed. */
    function renderSelectedChips(facetKey, panelEl) {
        const panel =
            panelEl ||
            (sectionsMount && sectionsMount.querySelector('[data-shop-facet="' + facetKey + '"]'));
        if (!panel) return;
        const wrap = panel.querySelector('[data-shop-selected]');
        if (!wrap) return;
        wrap.innerHTML = '';
        selectedOrder[facetKey].forEach((value) => {
            const chip = document.createElement('div');
            chip.className = 'sidebar-selected-chip';
            chip.appendChild(buildChipLeft(facetKey, value));
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sidebar-chip-remove';
            btn.setAttribute('aria-label', 'Remove filter');
            btn.textContent = '✕';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleSelection(facetKey, value);
            });
            chip.appendChild(btn);
            wrap.appendChild(chip);
        });
        const has = selectedOrder[facetKey].length > 0;
        panel.classList.toggle('shop-filter-panel--has-selections', has);
    }

    function paintFacetList(facetKey, panelEl) {
        const panel =
            panelEl ||
            (sectionsMount && sectionsMount.querySelector('[data-shop-facet="' + facetKey + '"]'));
        if (!panel) return;
        const list = panel.querySelector('[data-shop-list]');
        const search = panel.querySelector('[data-shop-search]');
        if (!list) return;
        const values = Array.isArray(panel._shopValues) ? panel._shopValues : [];
        const q = (search && search.value ? search.value : '').toLowerCase().trim();
        list.innerHTML = '';
        const selected = selectedOrder[facetKey];
        values
            .filter((value) => !selected.includes(value))
            .filter((value) => !q || String(value).toLowerCase().includes(q))
            .sort((a, b) => String(a).localeCompare(String(b), undefined, { sensitivity: 'base' }))
            .forEach((value) => {
                list.appendChild(buildPickerRow(facetKey, value));
            });
    }

    function bindSearchInput(search, facetKey) {
        if (!search || search._shopBound) return;
        search._shopBound = true;
        search.addEventListener('input', () => paintFacetList(facetKey));
        search.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') e.preventDefault();
        });
    }

    function buildSection(def, values) {
        const section = document.createElement('div');
        section.className = 'sidebar-section contribute-attr-panel shop-filter-panel shop-filter-panel--collapsed';
        section.setAttribute('data-shop-filter-panel', '');
        section.setAttribute('data-shop-facet', def.key);
        section._shopValues = values.slice();

        const headerBtn = document.createElement('button');
        headerBtn.type = 'button';
        headerBtn.className = 'shop-filter-panel-toggle';
        headerBtn.setAttribute('aria-expanded', 'false');
        headerBtn.setAttribute('aria-controls', 'shop-filter-body-' + def.key);
        const headerId = 'shop-filter-h-' + def.key;
        headerBtn.id = headerId;

        const row = document.createElement('div');
        row.className = 'contribute-attr-header-row shop-filter-panel-header-row';
        const title = document.createElement('span');
        title.className = 'contribute-attr-header-title';
        title.textContent = def.label;
        row.appendChild(title);
        headerBtn.appendChild(row);

        const selectedWrap = document.createElement('div');
        selectedWrap.className = 'sidebar-selected-items shop-filter-panel-selected-wrap';
        selectedWrap.setAttribute('data-shop-selected', def.key);

        const body = document.createElement('div');
        body.className = 'shop-filter-panel-body';
        body.id = 'shop-filter-body-' + def.key;
        body.setAttribute('role', 'region');
        body.setAttribute('aria-labelledby', headerId);

        const bodyInner = document.createElement('div');
        bodyInner.className = 'shop-filter-panel-body-inner';

        const search = document.createElement('input');
        search.type = 'search';
        search.className = 'sidebar-filter-search shop-filter-search-input';
        search.placeholder = 'Search…';
        search.setAttribute('data-shop-search', def.key);
        search.autocomplete = 'off';

        const list = document.createElement('div');
        list.className = 'sidebar-filter-list contribute-attr-picker-list';
        list.setAttribute('data-shop-list', def.key);

        bodyInner.appendChild(search);
        bodyInner.appendChild(list);
        body.appendChild(bodyInner);

        section.appendChild(headerBtn);
        section.appendChild(selectedWrap);
        section.appendChild(body);

        bindSearchInput(search, def.key);
        paintFacetList(def.key, section);
        renderSelectedChips(def.key, section);
        return section;
    }

    function paintFilterModeToggle(andBtn, orBtn) {
        if (!andBtn || !orBtn) return;
        const isAnd = filterMode === 'and';
        andBtn.setAttribute('aria-pressed', isAnd ? 'true' : 'false');
        orBtn.setAttribute('aria-pressed', isAnd ? 'false' : 'true');
        andBtn.classList.toggle('shop-filter-mode-btn--active', isAnd);
        orBtn.classList.toggle('shop-filter-mode-btn--active', !isAnd);
    }

    function buildFilterModeBar() {
        const wrap = document.createElement('div');
        wrap.className = 'shop-filter-mode';

        const label = document.createElement('span');
        label.className = 'shop-filter-mode-label';
        label.id = 'shop-filter-mode-label';
        label.textContent = 'Filter Mode:';

        const group = document.createElement('div');
        group.className = 'shop-filter-mode-toggle';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-labelledby', 'shop-filter-mode-label');

        const andBtn = document.createElement('button');
        andBtn.type = 'button';
        andBtn.className = 'shop-filter-mode-btn';
        andBtn.textContent = 'AND';
        andBtn.title = 'Show products that match every filter menu (and every tag within Made with, Made without, and Features)';

        const orBtn = document.createElement('button');
        orBtn.type = 'button';
        orBtn.className = 'shop-filter-mode-btn';
        orBtn.textContent = 'OR';
        orBtn.title = 'Show products that match any selected filter';

        function setMode(mode) {
            if (mode !== 'and' && mode !== 'or') return;
            filterMode = mode;
            persistFilterMode();
            paintFilterModeToggle(andBtn, orBtn);
            shopGridPage = 1;
            renderGrid();
        }

        andBtn.addEventListener('click', () => setMode('and'));
        orBtn.addEventListener('click', () => setMode('or'));

        group.appendChild(andBtn);
        group.appendChild(orBtn);
        wrap.appendChild(label);
        wrap.appendChild(group);

        paintFilterModeToggle(andBtn, orBtn);
        return wrap;
    }

    function buildSidebar(facets) {
        if (!sectionsMount) return;
        sectionsMount.innerHTML = '';
        sectionsMount.className = 'shop-filter-panels contribute-tab-picker-rows';

        const stack = document.createElement('div');
        stack.className = 'contribute-attr-column contribute-attr-column--stack shop-filter-stack';

        FACETS.forEach((def) => {
            const raw = facets[def.key];
            const values = Array.isArray(raw) ? raw.slice() : [];
            stack.appendChild(buildSection(def, values));
        });

        sectionsMount.appendChild(buildFilterModeBar());
        sectionsMount.appendChild(stack);
        wireShopAccordion(stack);
    }

    /**
     * Single expanded panel: opening one closes the others. Expanded panel sizes to its content
     * (chips + full option list); the filter stack scrolls vertically when needed (CSS).
     * Clicking the open panel header closes all.
     */
    function wireShopAccordion(stack) {
        const panels = stack.querySelectorAll('.shop-filter-panel');
        function collapseAll() {
            panels.forEach((panel) => {
                const btn = panel.querySelector('.shop-filter-panel-toggle');
                if (!btn) return;
                btn.setAttribute('aria-expanded', 'false');
                panel.classList.remove('shop-filter-panel--expanded');
                panel.classList.remove('shop-filter-panel--accordion-settled');
                panel.classList.add('shop-filter-panel--collapsed');
            });
        }
        function expandPanel(panel) {
            const btn = panel.querySelector('.shop-filter-panel-toggle');
            if (!btn) return;
            collapseAll();
            btn.setAttribute('aria-expanded', 'true');
            panel.classList.add('shop-filter-panel--expanded');
            panel.classList.remove('shop-filter-panel--collapsed');

            const body = panel.querySelector('.shop-filter-panel-body');
            if (!body) return;

            panel.classList.remove('shop-filter-panel--accordion-settled');

            function markSettled() {
                panel.classList.add('shop-filter-panel--accordion-settled');
            }

            function onTransitionEnd(e) {
                if (e.target !== body || e.propertyName !== 'grid-template-rows') return;
                body.removeEventListener('transitionend', onTransitionEnd);
                window.clearTimeout(fallbackTimer);
                markSettled();
            }

            body.addEventListener('transitionend', onTransitionEnd);
            const fallbackTimer = window.setTimeout(function () {
                body.removeEventListener('transitionend', onTransitionEnd);
                markSettled();
            }, 400);
        }
        panels.forEach((panel) => {
            const btn = panel.querySelector('.shop-filter-panel-toggle');
            if (!btn) return;
            btn.addEventListener('click', () => {
                if (panel.classList.contains('shop-filter-panel--expanded')) {
                    collapseAll();
                } else {
                    expandPanel(panel);
                }
            });
        });
    }

    async function init() {
        await refreshFavorites();
        let data;
        try {
            const url = searchMode
                ? '/api/products/search?' + new URLSearchParams({ q: searchQuery }).toString()
                : slugLower === 'all'
                  ? '/api/products/all'
                  : '/api/products/category/' + encodeURIComponent(slugRaw);
            const res = await fetch(url, {
                cache: 'no-store',
            });
            if (!res.ok) {
                const errText = await res.text().catch(() => '');
                console.error('Shop catalog fetch failed', res.status, url, errText);
                if (emptyEl) {
                    emptyEl.hidden = false;
                    emptyEl.textContent =
                        'Could not load products. If this persists, check the server log.';
                }
                return;
            }
            data = await res.json();
        } catch (e) {
            console.error(e);
            if (emptyEl) {
                emptyEl.hidden = false;
                emptyEl.textContent = 'Could not load products (network error).';
            }
            return;
        }

        /* Category: /api/products/category/…; slug ``all``: /api/products/all; search: /api/products/search?q=… */
        allProducts = Array.isArray(data.products) ? data.products : [];
        brandLogos = (data.facets && data.facets.brand_logos) || {};
        certificationImages =
            data.facets && data.facets.certification_images && typeof data.facets.certification_images === 'object'
                ? data.facets.certification_images
                : {};
        UI.primeTagColorsFromProducts(allProducts);

        const facets = data.facets || {};
        loadPersistedFilterMode();
        buildSidebar(facets);
        bindShopSidebarResetButton();
        renderGrid();
    }

    bindShopGridFooter();
    init();
})();
