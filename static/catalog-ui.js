/**
 * Shared product card + detail modal (matches homepage rendering).
 */
(function (global) {
    const CERT_IMAGE_MAP = {
        'FSC-Certified': '/certifications/fsc.png',
        'USDA Organic': '/certifications/usdaorganic.png'
    };

    const FLAG_NAME_MAP = {
        'United States': 'America',
        'United Kingdom': 'England',
        Netherlands: 'the_Netherlands',
        Philippines: 'the_Philippines',
        'Czech Republic': 'the_Czech_Republic',
        'New Zealand': 'New_Zealand',
        'South Korea': 'South_Korea',
        'South Africa': 'South_Africa'
    };

    function getFlagUrl(country) {
        const raw = country != null && String(country).trim() !== ''
            ? (FLAG_NAME_MAP[country] ?? country)
            : '';
        const name = String(raw).replace(/\s+/g, '_');
        return `/flags/Flag_of_${name}.svg`;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function safeExternalHttpUrl(url) {
        const s = String(url == null ? '' : url).trim();
        if (!s) return '';
        const lower = s.toLowerCase();
        if (!lower.startsWith('http://') && !lower.startsWith('https://')) return '';
        return s;
    }

    function brandLinkUrl(product) {
        if (!product) return '';
        let raw = product.brand_link;
        if (raw == null || String(raw).trim() === '') {
            raw = product.brand && product.brand.link;
        }
        return safeExternalHttpUrl(raw);
    }

    function certLinkUrl(c) {
        if (!c || typeof c !== 'object') return '';
        return safeExternalHttpUrl(c.link);
    }

    /** Click target for closest(); Text nodes are not Elements and would break closest('a'). */
    function eventClickTargetElement(ev) {
        const t = ev && ev.target;
        if (!t) return null;
        if (typeof Element !== 'undefined' && t instanceof Element) return t;
        return t.parentElement || null;
    }

    function formatProductCategoryLabel(product) {
        const main = String((product && (product.main_category || product.category)) || '').trim();
        const sub = String((product && product.subcategory) || '').trim();
        if (main && sub) return `${main} / ${sub}`;
        return main || sub;
    }

    /** Turn `/kitchen` or `/kitchen/cookware` into a full URL using the current origin (e.g. http://127.0.0.1:8000/...). */
    function shopCategoryAbsoluteUrl(pathOrUrl) {
        if (pathOrUrl == null || pathOrUrl === '') return '';
        const s = String(pathOrUrl).trim();
        if (!s) return '';
        if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return s;
        try {
            return new URL(s.startsWith('/') ? s : `/${s}`, global.location.origin).href;
        } catch (e) {
            return s;
        }
    }

    /** Category / Subcategory line for product cards (blue link styling; real <a> when hrefs exist). */
    function buildProductCardCategoryHtml(product) {
        const main = String((product && (product.main_category || product.category)) || '').trim();
        const sub = String((product && product.subcategory) || '').trim();
        const mh = product && product.category_main_href;
        const sh = product && product.category_sub_href;
        if (!main && !sub) {
            return '<span class="product-card-category-line product-card-category-line--empty"></span>';
        }
        const mainEsc = escapeHtml(main);
        const subEsc = escapeHtml(sub);
        const linkMain = (href, text) =>
            href
                ? `<a href="${escapeHtml(shopCategoryAbsoluteUrl(href))}" class="product-card-category-link">${text}</a>`
                : `<span class="product-card-category-link product-card-category-nolink">${text}</span>`;
        if (main && sub) {
            return `<span class="product-card-category-line">${linkMain(mh, mainEsc)}<span class="product-card-category-sep"> / </span>${linkMain(sh, subEsc)}</span>`;
        }
        if (main) {
            return `<span class="product-card-category-line">${linkMain(mh, mainEsc)}</span>`;
        }
        return `<span class="product-card-category-line">${linkMain(sh, subEsc)}</span>`;
    }

    const COLORS = Array.from({ length: 1000 }, (_, i) => {
        const hue = (i * 137.5) % 360;
        const saturation = 60 + ((i * 17) % 30);
        const lightness = 50 + ((i * 23) % 30);
        return `hsl(${hue}, ${saturation}%, ${lightness}%)`;
    });

    const TAG_COLOR_STORAGE_KEY = 'tabbed_tag_colors';

    const tagColorMap = {};
    let globalColorIndex = 0;

    function loadPersistedTagColors() {
        try {
            const raw = global.localStorage && global.localStorage.getItem(TAG_COLOR_STORAGE_KEY);
            if (!raw) return;
            const parsed = JSON.parse(raw);
            if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return;
            Object.keys(parsed).forEach((k) => {
                const v = parsed[k];
                if (typeof v === 'string' && v.length < 200) {
                    tagColorMap[k] = v;
                }
            });
            globalColorIndex = Object.keys(tagColorMap).length;
        } catch (e) {
            /* ignore */
        }
    }

    function savePersistedTagColors() {
        try {
            if (global.localStorage) {
                global.localStorage.setItem(TAG_COLOR_STORAGE_KEY, JSON.stringify(tagColorMap));
            }
        } catch (e) {
            /* ignore quota / private mode */
        }
    }

    function colorHueDeg(color) {
        const m = String(color || '').match(/hsl\(\s*([0-9.]+)\s*,/i);
        if (!m) return null;
        const n = Number(m[1]);
        if (!Number.isFinite(n)) return null;
        return ((n % 360) + 360) % 360;
    }

    function hueDistance(a, b) {
        const d = Math.abs(a - b) % 360;
        return Math.min(d, 360 - d);
    }

    function clampByte(n) {
        const v = Number(n);
        if (!Number.isFinite(v)) return 0;
        return Math.max(0, Math.min(255, Math.round(v)));
    }

    function hslToRgb(h, s, l) {
        const hh = (((Number(h) || 0) % 360) + 360) % 360;
        const ss = Math.max(0, Math.min(1, (Number(s) || 0) / 100));
        const ll = Math.max(0, Math.min(1, (Number(l) || 0) / 100));
        const c = (1 - Math.abs(2 * ll - 1)) * ss;
        const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
        const m = ll - c / 2;
        let r1 = 0;
        let g1 = 0;
        let b1 = 0;
        if (hh < 60) {
            r1 = c; g1 = x; b1 = 0;
        } else if (hh < 120) {
            r1 = x; g1 = c; b1 = 0;
        } else if (hh < 180) {
            r1 = 0; g1 = c; b1 = x;
        } else if (hh < 240) {
            r1 = 0; g1 = x; b1 = c;
        } else if (hh < 300) {
            r1 = x; g1 = 0; b1 = c;
        } else {
            r1 = c; g1 = 0; b1 = x;
        }
        return {
            r: clampByte((r1 + m) * 255),
            g: clampByte((g1 + m) * 255),
            b: clampByte((b1 + m) * 255),
        };
    }

    function cssColorToRgb(color) {
        const s = String(color || '').trim();
        if (!s) return null;
        const hex3 = s.match(/^#([0-9a-f]{3})$/i);
        if (hex3) {
            const h = hex3[1];
            return {
                r: parseInt(h[0] + h[0], 16),
                g: parseInt(h[1] + h[1], 16),
                b: parseInt(h[2] + h[2], 16),
            };
        }
        const hex6 = s.match(/^#([0-9a-f]{6})$/i);
        if (hex6) {
            const h = hex6[1];
            return {
                r: parseInt(h.slice(0, 2), 16),
                g: parseInt(h.slice(2, 4), 16),
                b: parseInt(h.slice(4, 6), 16),
            };
        }
        const rgb = s.match(/^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)/i);
        if (rgb) {
            return {
                r: clampByte(rgb[1]),
                g: clampByte(rgb[2]),
                b: clampByte(rgb[3]),
            };
        }
        const hsl = s.match(/^hsla?\(\s*([0-9.+-]+)\s*,\s*([0-9.+-]+)%\s*,\s*([0-9.+-]+)%/i);
        if (hsl) {
            return hslToRgb(Number(hsl[1]), Number(hsl[2]), Number(hsl[3]));
        }
        return null;
    }

    function rgbDistance(a, b) {
        const dr = a.r - b.r;
        const dg = a.g - b.g;
        const db = a.b - b.b;
        return Math.sqrt((dr * dr) + (dg * dg) + (db * db));
    }

    function rgbToCss(rgb) {
        return `rgb(${clampByte(rgb.r)}, ${clampByte(rgb.g)}, ${clampByte(rgb.b)})`;
    }

    function invertRgb(rgb) {
        return { r: 255 - rgb.r, g: 255 - rgb.g, b: 255 - rgb.b };
    }

    function invertChannel(rgb, ch) {
        return {
            r: ch === 'r' ? 255 - rgb.r : rgb.r,
            g: ch === 'g' ? 255 - rgb.g : rgb.g,
            b: ch === 'b' ? 255 - rgb.b : rgb.b,
        };
    }

    function pickInvertedContrastColor(existingColors, fallbackColor) {
        const existing = (existingColors || [])
            .map(cssColorToRgb)
            .filter((c) => c != null);
        if (!existing.length) return fallbackColor;
        const anchor = existing[existing.length - 1];
        const fallback = cssColorToRgb(fallbackColor) || { r: 128, g: 128, b: 128 };
        const candidates = [
            invertRgb(anchor),
            invertChannel(anchor, 'r'),
            invertChannel(anchor, 'g'),
            invertChannel(anchor, 'b'),
            invertRgb(fallback),
            fallback,
        ];
        let best = candidates[0];
        let bestScore = -1;
        for (let i = 0; i < candidates.length; i++) {
            const cand = candidates[i];
            let minDist = Number.POSITIVE_INFINITY;
            for (let j = 0; j < existing.length; j++) {
                minDist = Math.min(minDist, rgbDistance(cand, existing[j]));
            }
            if (minDist > bestScore) {
                bestScore = minDist;
                best = cand;
            }
        }
        return rgbToCss(best);
    }

    /**
     * Pick a contrasting palette color relative to colors already present in one section.
     * Falls back to sequential selection when no section colors are available.
     */
    function pickContrastingColor(existingColors) {
        const usedHues = (existingColors || [])
            .map(colorHueDeg)
            .filter((h) => h != null);
        if (!usedHues.length) {
            const c = COLORS[globalColorIndex % COLORS.length];
            globalColorIndex++;
            return c;
        }

        let bestColor = COLORS[globalColorIndex % COLORS.length];
        let bestScore = -1;
        let bestIndex = globalColorIndex % COLORS.length;

        for (let step = 0; step < COLORS.length; step++) {
            const idx = (globalColorIndex + step) % COLORS.length;
            const cand = COLORS[idx];
            const hue = colorHueDeg(cand);
            if (hue == null) continue;
            let minDist = 360;
            for (let i = 0; i < usedHues.length; i++) {
                const d = hueDistance(hue, usedHues[i]);
                if (d < minDist) minDist = d;
            }
            if (minDist > bestScore) {
                bestScore = minDist;
                bestColor = cand;
                bestIndex = idx;
            }
        }

        globalColorIndex = (bestIndex + 1) % COLORS.length;
        return bestColor;
    }

    function isTooSimilarToSection(color, sectionExistingColors) {
        const hue = colorHueDeg(color);
        if (hue == null) return false;
        const used = (sectionExistingColors || [])
            .map(colorHueDeg)
            .filter((h) => h != null);
        if (!used.length) return false;
        const MIN_HUE_DISTANCE = 42;
        for (let i = 0; i < used.length; i++) {
            if (hueDistance(hue, used[i]) < MIN_HUE_DISTANCE) return true;
        }
        return false;
    }

    function getColorForTag(tag, _sectionExistingColors) {
        // Each tag must render the SAME color everywhere it appears, regardless
        // of which other tags happen to sit next to it in any given product
        // card. The persisted ``tagColorMap`` is the single source of truth;
        // the legacy per-section "inverted contrast" override caused the same
        // tag (e.g. "Vegan") to flip colors product-to-product, so it's
        // intentionally ignored here.
        const key = tag == null ? '' : String(tag);
        if (!key) {
            return COLORS[0];
        }
        if (!tagColorMap[key]) {
            tagColorMap[key] = pickContrastingColor([]);
            savePersistedTagColors();
        }
        return tagColorMap[key];
    }

    loadPersistedTagColors();

    function tabbedCertName(c) {
        if (c == null) return '';
        if (typeof c === 'string') return String(c).trim();
        return String((c && c.name) || '').trim();
    }

    function certIconUrl(c) {
        if (typeof c === 'string') {
            return CERT_IMAGE_MAP[c] || null;
        }
        if (c && c.image_url) {
            return c.image_url;
        }
        if (c && c.image_filename) {
            return '/uploads/' + c.image_filename;
        }
        const n = tabbedCertName(c);
        return n ? CERT_IMAGE_MAP[n] || null : null;
    }

    /** Flat section: title + lines (no panel border). */
    function buildModalFlatSectionHtml(title, bodyInnerHtml, prose) {
        const bodyClass = prose
            ? 'modal-flat-section-body modal-flat-section-body--prose'
            : 'modal-flat-section-body modal-flat-lines';
        return (
            '<section class="modal-flat-section">' +
            '<div class="modal-flat-section-title">' +
            escapeHtml(title) +
            '</div>' +
            '<div class="' +
            bodyClass +
            '">' +
            bodyInnerHtml +
            '</div>' +
            '</section>'
        );
    }

    /** Body HTML for flat "Made In" section (flag + country). */
    function buildModalMadeInSectionBodyHtml(countryRaw) {
        const t = String(countryRaw || '').trim();
        if (!t) {
            return '<div class="modal-detail-line modal-detail-line--muted">—</div>';
        }
        return (
            '<div class="modal-detail-line">' +
            '<span class="modal-made-in-value">' +
            '<img src="' +
            escapeHtml(getFlagUrl(t)) +
            '" alt="" class="product-flag modal-made-in-flag" onerror="this.style.visibility=\'hidden\'">' +
            '<span class="modal-made-in-country">' +
            escapeHtml(t) +
            '</span>' +
            '</span></div>'
        );
    }

    /** One certification row: icon + label, no chip border (expanded modal). */
    function buildModalCertLineHtml(c) {
        const label = tabbedCertName(c);
        if (!label) return '';
        const href = certLinkUrl(c);
        const iconSrc = certIconUrl(c);
        const imgHtml = iconSrc
            ? `<img src="${escapeHtml(iconSrc)}" alt="${escapeHtml(label)}" title="${escapeHtml(label)}" class="modal-cert-line-icon" loading="lazy" decoding="async">`
            : '';
        const labelHtml = `<span class="modal-cert-line-text">${escapeHtml(label)}</span>`;
        const inner = `<span class="modal-cert-line-inner">${imgHtml}${labelHtml}</span>`;
        if (href) {
            return `<a href="${escapeHtml(href)}" class="modal-detail-line modal-detail-line--link" target="_blank" rel="noopener noreferrer">${inner}</a>`;
        }
        return `<div class="modal-detail-line">${inner}</div>`;
    }

    function buildModalTagLineHtml(tag, color) {
        return (
            `<div class="modal-detail-line">` +
            `<span class="modal-tag">` +
            `<span class="modal-tag-color" style="background-color:${color}"></span>` +
            `<span>${escapeHtml(String(tag))}</span>` +
            `</span>` +
            `</div>`
        );
    }

    /**
     * @param {HTMLElement} parent
     * @param {object} c certification
     * @param {boolean} affiliate when true, cert links use click handlers (embed inside affiliate link shell)
     */
    function appendModalCertElement(parent, c, affiliate) {
        const label = tabbedCertName(c);
        if (!label) return;
        const href = certLinkUrl(c);
        const iconSrc = certIconUrl(c);
        function addInnerTo(el) {
            const inner = document.createElement('span');
            inner.className = 'modal-cert-line-inner';
            if (iconSrc) {
                const img = document.createElement('img');
                img.className = 'modal-cert-line-icon';
                img.src = iconSrc;
                img.alt = label;
                img.title = label;
                img.loading = 'lazy';
                inner.appendChild(img);
            }
            const lab = document.createElement('span');
            lab.className = 'modal-cert-line-text';
            lab.textContent = label;
            inner.appendChild(lab);
            el.appendChild(inner);
        }
        if (href) {
            if (affiliate) {
                const sp = document.createElement('span');
                sp.className = 'modal-detail-line modal-detail-line--link';
                sp.tabIndex = 0;
                sp.setAttribute('role', 'link');
                addInnerTo(sp);
                sp.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    global.open(href, '_blank', 'noopener,noreferrer');
                });
                sp.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        e.stopPropagation();
                        global.open(href, '_blank', 'noopener,noreferrer');
                    }
                });
                parent.appendChild(sp);
            } else {
                const a = document.createElement('a');
                a.className = 'modal-detail-line modal-detail-line--link';
                a.href = href;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                addInnerTo(a);
                parent.appendChild(a);
            }
        } else {
            const sp = document.createElement('div');
            sp.className = 'modal-detail-line';
            addInnerTo(sp);
            parent.appendChild(sp);
        }
    }

    function brandLogoUrl(product) {
        if (product.brand_image_url) return product.brand_image_url;
        if (product.brand && product.brand.image_url) return product.brand.image_url;
        if (product.brand_image_filename) return '/uploads/' + product.brand_image_filename;
        return null;
    }

    function primeTagColorsFromProducts(products) {
        (products || []).forEach((p) => {
            (p.made_with || []).forEach(getColorForTag);
            (p.made_without || []).forEach(getColorForTag);
            (p.attributes || []).forEach(getColorForTag);
            (p.certifications || []).forEach((c) => {
                const n = tabbedCertName(c);
                if (n) getColorForTag(n);
            });
        });
    }

    /**
     * @param {object} product
     * @param {{ isFavorite: (id: *) => boolean, onToggleFavorite: (id: *) => void, onOpenDetail?: (p: object) => void }} handlers
     */
    function createProductCard(product, handlers) {
        const { isFavorite, onToggleFavorite, onOpenDetail } = handlers;

        const card = document.createElement('div');
        card.className = 'product-card';

        const mainContent = document.createElement('div');
        mainContent.className = 'product-main-content product-main-content--grid';

        const favClass = isFavorite(product.id) ? ' favorited' : '';
        const heartSvg = isFavorite(product.id) ? '/static/heart-filled.svg' : '/static/heart.svg';

        const certImgs = (product.certifications || [])
            .map((c) => {
                const src = certIconUrl(c);
                const label = tabbedCertName(c);
                if (!src || !label) return '';
                const href = certLinkUrl(c);
                const srcEsc = escapeHtml(src);
                const img = `<img src="${srcEsc}" alt="${escapeHtml(label)}" title="${escapeHtml(label)}" class="product-cert-icon">`;
                if (!href) return img;
                return `<a href="${escapeHtml(href)}" class="product-cert-link" target="_blank" rel="noopener noreferrer" title="${escapeHtml(label)}">${img}</a>`;
            })
            .join('');

        const brandNameEsc = escapeHtml(product.brand_name || '');
        const nameEsc = escapeHtml(product.name || '');
        const categoryLineHtml = buildProductCardCategoryHtml(product);
        const logoSrc = brandLogoUrl(product);
        const brandHref = brandLinkUrl(product);
        const logoSrcEsc = logoSrc ? escapeHtml(logoSrc) : '';
        const brandLogoSlot = logoSrc
            ? brandHref
                ? `<span class="product-brand-logo-slot"><a href="${escapeHtml(brandHref)}" class="product-brand-logo-link" target="_blank" rel="noopener noreferrer"><img class="product-brand-img" src="${logoSrcEsc}" alt="${brandNameEsc}" loading="lazy" decoding="async"></a></span>`
                : `<span class="product-brand-logo-slot"><img class="product-brand-img" src="${logoSrcEsc}" alt="${brandNameEsc}" loading="lazy" decoding="async"></span>`
            : '<span class="product-brand-logo-slot product-brand-logo-slot--empty" aria-hidden="true"></span>';

        const countryRaw = String(product.made_in || '').trim();
        const madeInFullEsc = countryRaw ? escapeHtml(`Made in: ${countryRaw}`) : '';
        const madeInAttrs = countryRaw
            ? ` data-tooltip="${madeInFullEsc}" aria-label="${madeInFullEsc}"`
            : '';

        const isAffiliateBuy = !!product.earns_commission && !!product.product_link;
        const buyBtnClass = isAffiliateBuy
            ? 'buy-button buy-button--affiliate header-login-btn'
            : 'buy-button header-login-btn';
        const priceNum = Number(product.price);
        const buyInner = Number.isFinite(priceNum)
            ? `$${priceNum.toFixed(2)}`
            : '$0.00';
        const buyControl = product.product_link
            ? `<a href="${escapeHtml(product.product_link)}" class="${buyBtnClass}" target="_blank" rel="noopener noreferrer">${buyInner}</a>`
            : `<button type="button" class="${buyBtnClass}">${buyInner}</button>`;

        const certsTopHtml = certImgs
            ? `<div class="product-top-inline-certs">
                                <div class="product-cert-row product-cert-row--card-strip">${certImgs}</div>
                            </div>`
            : '<div class="product-top-inline-certs"></div>';

        const topSection = document.createElement('div');
        topSection.className = 'product-top-section';
        topSection.innerHTML = `
                    <div class="product-top-inner">
                        <div class="product-top-leading">
                            <span class="product-made-in-compact"${madeInAttrs}>
                                <span class="made-in-label">Made in</span>
                                <img src="${getFlagUrl(product.made_in)}" alt="" class="product-flag" onerror="this.style.visibility='hidden'">
                            </span>
                        </div>
                        <div class="product-top-trailing">
                            ${certsTopHtml}
                            <div class="product-top-actions">
                                <button type="button" class="favorite-heart${favClass}" data-product-id="${product.id}">
                                    <img src="${heartSvg}" alt="Favorite" class="favorite-heart-icon">
                                </button>
                            </div>
                        </div>
                    </div>
                `;

        topSection.querySelector('.favorite-heart').addEventListener('click', (e) => {
            e.stopPropagation();
            onToggleFavorite(product.id);
        });

        topSection.querySelectorAll('.product-cert-row a[href]').forEach((a) => {
            a.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        });

        const middleSection = document.createElement('div');
        middleSection.className = 'product-image-section';
        const imageSrc = product.product_image_filename
            ? `/uploads/${product.product_image_filename}`
            : '/static/haws.jpg';
        middleSection.innerHTML = `<img src="${imageSrc}" alt="${nameEsc}" class="product-image" onerror="this.src='/static/haws.jpg'">`;

        const bottomSection = document.createElement('div');
        bottomSection.className = 'product-bottom-section';
        bottomSection.innerHTML = `
                    <div class="product-brand product-brand--bottom-bar">
                        ${brandLogoSlot}
                    </div>
                    <div class="product-bottom-text">
                        ${categoryLineHtml}
                        <h3 class="product-name">${nameEsc}</h3>
                    </div>
                    ${buyControl}
                `;

        bottomSection.querySelectorAll('.product-card-category-line a[href]').forEach((a) => {
            a.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        });

        bottomSection.querySelectorAll('.product-brand-logo-slot a[href]').forEach((a) => {
            a.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        });

        mainContent.appendChild(topSection);
        mainContent.appendChild(middleSection);
        mainContent.appendChild(bottomSection);

        const productTabs = document.createElement('div');
        productTabs.className = 'product-tabs';

        const madeWithList = Array.isArray(product.made_with)
            ? product.made_with
            : Array.isArray(product.madeWith)
                ? product.madeWith
                : [];
        const madeWithoutList = Array.isArray(product.made_without)
            ? product.made_without
            : Array.isArray(product.madeWithout)
                ? product.madeWithout
                : [];
        const featuresList = Array.isArray(product.attributes)
            ? product.attributes
            : Array.isArray(product.features)
                ? product.features
                : [];

        let tabSections = [
            { items: madeWithList, bgColor: '#c8e6c9', tooltipPrefix: 'Made With' },
            { items: madeWithoutList, bgColor: '#ffcdd2', tooltipPrefix: 'Made Without' },
            { items: featuresList, bgColor: '#e3f2fd', tooltipPrefix: 'Features' }
        ].filter((s) => (s.items || []).length > 0);

        if (!tabSections.length) {
            // Preserve legacy empty-state appearance when no tag-like metadata exists.
            tabSections = [
                { items: [], bgColor: '#c8e6c9', tooltipPrefix: 'Made With' },
                { items: [], bgColor: '#ffcdd2', tooltipPrefix: 'Made Without' }
            ];
        }

        tabSections.forEach(({ items, bgColor, tooltipPrefix }) => {
            const section = document.createElement('div');
            section.className = 'product-tab-section';
            section.style.backgroundColor = bgColor;
            section.style.flex = `${Math.max(1, items.length)} 1 0`;

            const itemsContainer = document.createElement('div');
            itemsContainer.className = 'product-tab-items-container';

            const sectionColors = [];
            items.forEach((tag) => {
                const tagEl = document.createElement('div');
                tagEl.className = 'product-tab-item';
                const c = getColorForTag(tag, sectionColors);
                sectionColors.push(c);
                tagEl.style.backgroundColor = c;
                tagEl.setAttribute('data-tooltip', `${tooltipPrefix}: ${tag}`);
                tagEl.textContent = tag;
                itemsContainer.appendChild(tagEl);
            });

            section.appendChild(itemsContainer);
            productTabs.appendChild(section);
        });

        card.appendChild(mainContent);
        card.appendChild(productTabs);

        const openDetail = onOpenDetail || function (p) {
            openProductModal(p, handlers);
        };

        card.addEventListener('click', (e) => {
            const el = eventClickTargetElement(e);
            if (!el) return;
            if (el.closest('.buy-button')) return;
            if (el.closest('a[href]')) return;
            openDetail(product);
        });

        return card;
    }

    function createModalTagLineEl(t) {
        const row = document.createElement('div');
        row.className = 'modal-detail-line';
        const dot = document.createElement('span');
        dot.className = 'modal-detail-line-dot';
        dot.style.backgroundColor = getColorForTag(t);
        row.appendChild(dot);
        row.appendChild(document.createTextNode(t));
        return row;
    }

    /**
     * Expanded product block for article editor / published HTML: same content as the homepage
     * detail modal, without close control; entire block is one link; buy is a styled label at bottom-right.
     * @param {object} product
     * @returns {HTMLDivElement}
     */
    function createArticleProductExpandedEmbed(product) {
        const wrap = document.createElement('div');
        wrap.className = 'article-product-embed article-product-embed-expanded';

        const affiliate = (product.product_link || '').trim();

        const shell = affiliate
            ? document.createElement('a')
            : document.createElement('div');
        shell.className = affiliate
            ? 'article-product-embed-link'
            : 'article-product-embed-link article-product-embed-link--static';
        if (affiliate) {
            shell.href = affiliate;
            shell.target = '_blank';
            shell.rel = 'noopener noreferrer';
        }

        const card = document.createElement('div');
        card.className = 'modal-card article-embed-modal-card';

        const modalBody = document.createElement('div');
        modalBody.className = 'modal-body';

        const imageSrc = product.product_image_filename
            ? `/uploads/${product.product_image_filename}`
            : '/static/haws.jpg';
        const imageCol = document.createElement('div');
        imageCol.className = 'modal-image-col';
        const img = document.createElement('img');
        img.className = 'modal-image';
        img.src = imageSrc;
        img.alt = product.name || '';
        img.addEventListener('error', function () {
            this.src = '/static/haws.jpg';
        });
        imageCol.appendChild(img);

        const details = document.createElement('div');
        details.className = 'modal-details-col';

        const headerBrand = document.createElement('div');
        headerBrand.className = 'modal-header-brand';
        const brandP = document.createElement('p');
        brandP.className = 'modal-brand';
        const modalBrandSrc = brandLogoUrl(product);
        const embedBrandHref = brandLinkUrl(product);
        if (modalBrandSrc) {
            const bimg = document.createElement('img');
            bimg.className = 'modal-brand-img';
            bimg.src = modalBrandSrc;
            bimg.alt = product.brand_name || '';
            if (embedBrandHref) {
                if (affiliate) {
                    const w = document.createElement('span');
                    w.className = 'modal-brand-logo-link';
                    w.tabIndex = 0;
                    w.setAttribute('role', 'link');
                    w.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        global.open(embedBrandHref, '_blank', 'noopener,noreferrer');
                    });
                    w.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            e.stopPropagation();
                            global.open(embedBrandHref, '_blank', 'noopener,noreferrer');
                        }
                    });
                    w.appendChild(bimg);
                    brandP.appendChild(w);
                } else {
                    const a = document.createElement('a');
                    a.className = 'modal-brand-logo-link';
                    a.href = embedBrandHref;
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    a.appendChild(bimg);
                    brandP.appendChild(a);
                }
            } else {
                brandP.appendChild(bimg);
            }
        } else {
            brandP.textContent = product.brand_name || '';
        }
        headerBrand.appendChild(brandP);

        const nameH2 = document.createElement('h2');
        nameH2.className = 'modal-product-name modal-product-name--header';
        nameH2.textContent = product.name || '';

        const catSpan = document.createElement('span');
        catSpan.className = 'modal-category';
        catSpan.textContent = formatProductCategoryLabel(product);
        const headerSub = document.createElement('div');
        headerSub.className = 'modal-header-sub';

        const heartSpan = document.createElement('span');
        heartSpan.className = 'favorite-heart modal-header-embed-heart';
        heartSpan.setAttribute('aria-hidden', 'true');
        const heartImg = document.createElement('img');
        heartImg.className = 'favorite-heart-icon';
        heartImg.src = '/static/heart.svg';
        heartImg.alt = '';
        heartSpan.appendChild(heartImg);
        headerSub.appendChild(heartSpan);
        headerSub.appendChild(catSpan);

        const brandRow = document.createElement('div');
        brandRow.className = 'modal-header-brand-row';
        brandRow.appendChild(headerBrand);

        const isAffiliateBuyEmbed = !!product.earns_commission && !!affiliate;
        const brandActions = document.createElement('div');
        brandActions.className = 'modal-header-brand-actions';
        const buyEl = affiliate
            ? document.createElement('a')
            : document.createElement('span');
        if (affiliate) {
            buyEl.href = affiliate;
            buyEl.target = '_blank';
            buyEl.rel = 'noopener noreferrer';
        }
        buyEl.className = isAffiliateBuyEmbed
            ? 'buy-button buy-button--affiliate header-login-btn modal-buy-button modal-buy-button--embed'
            : 'buy-button header-login-btn modal-buy-button modal-buy-button--embed';
        const embedPriceNum = Number(product.price);
        buyEl.textContent = Number.isFinite(embedPriceNum)
            ? `$${embedPriceNum.toFixed(2)}`
            : '$0.00';
        brandActions.appendChild(buyEl);
        brandRow.appendChild(brandActions);

        const titleRow = document.createElement('div');
        titleRow.className = 'modal-header-title-row';
        titleRow.appendChild(nameH2);

        const brandLine = document.createElement('div');
        brandLine.className = 'modal-header-brand-line';
        brandLine.appendChild(brandRow);
        brandLine.appendChild(headerSub);
        brandLine.appendChild(titleRow);

        const modalHeaderTop = document.createElement('div');
        modalHeaderTop.className = 'modal-header-top';
        modalHeaderTop.appendChild(brandLine);

        const descTrim = (product.description || '').trim();
        const descP = document.createElement('p');
        descP.className = 'modal-description' + (descTrim ? '' : ' is-empty');
        descP.textContent = descTrim || 'No description provided.';

        function appendFlatSection(parent, title, fillLines) {
            const sec = document.createElement('section');
            sec.className = 'modal-flat-section';
            const ht = document.createElement('div');
            ht.className = 'modal-flat-section-title';
            ht.textContent = title;
            const body = document.createElement('div');
            body.className = 'modal-flat-section-body modal-flat-lines';
            fillLines(body);
            sec.appendChild(ht);
            sec.appendChild(body);
            parent.appendChild(sec);
        }

        function mutedNone(body) {
            const line = document.createElement('div');
            line.className = 'modal-detail-line modal-detail-line--muted';
            line.textContent = 'None';
            body.appendChild(line);
        }

        const metadataStack = document.createElement('div');
        metadataStack.className = 'modal-metadata-stack modal-metadata-stack--flat';

        const descSec = document.createElement('section');
        descSec.className = 'modal-flat-section';
        descSec.setAttribute('aria-label', 'Description');
        const descTitle = document.createElement('div');
        descTitle.className = 'modal-flat-section-title';
        descTitle.textContent = 'Description';
        const descBody = document.createElement('div');
        descBody.className = 'modal-flat-section-body modal-flat-section-body--prose';
        descBody.appendChild(descP);
        descSec.appendChild(descTitle);
        descSec.appendChild(descBody);
        metadataStack.appendChild(descSec);

        appendFlatSection(metadataStack, 'Made In', (body) => {
            const t = String((product && product.made_in) || '').trim();
            if (!t) {
                const dash = document.createElement('div');
                dash.className = 'modal-detail-line modal-detail-line--muted';
                dash.textContent = '—';
                body.appendChild(dash);
            } else {
                const line = document.createElement('div');
                line.className = 'modal-detail-line';
                const wrap = document.createElement('span');
                wrap.className = 'modal-made-in-value';
                const c = document.createElement('span');
                c.className = 'modal-made-in-country';
                c.textContent = t;
                const img = document.createElement('img');
                img.className = 'product-flag modal-made-in-flag';
                img.src = getFlagUrl(t);
                img.alt = '';
                img.addEventListener('error', function () {
                    this.style.visibility = 'hidden';
                });
                wrap.appendChild(img);
                wrap.appendChild(c);
                line.appendChild(wrap);
                body.appendChild(line);
            }
        });

        const certs = product.certifications || [];
        appendFlatSection(metadataStack, 'Certifications', (body) => {
            if (certs.length === 0) mutedNone(body);
            else certs.forEach((c) => appendModalCertElement(body, c, !!affiliate));
        });

        appendFlatSection(metadataStack, 'Made With', (body) => {
            const list = product.made_with || [];
            if (list.length === 0) mutedNone(body);
            else list.forEach((t) => body.appendChild(createModalTagLineEl(t)));
        });

        appendFlatSection(metadataStack, 'Made Without', (body) => {
            const list = product.made_without || [];
            if (list.length === 0) mutedNone(body);
            else list.forEach((t) => body.appendChild(createModalTagLineEl(t)));
        });

        appendFlatSection(metadataStack, 'Features', (body) => {
            const list = product.attributes || [];
            if (list.length === 0) mutedNone(body);
            else list.forEach((a) => body.appendChild(createModalTagLineEl(String(a))));
        });

        details.appendChild(modalHeaderTop);
        details.appendChild(metadataStack);

        modalBody.appendChild(imageCol);
        modalBody.appendChild(details);

        card.appendChild(modalBody);
        shell.appendChild(card);
        wrap.appendChild(shell);

        return wrap;
    }

    function openProductModal(product, handlers) {
        const { isFavorite, onToggleFavorite } = handlers;
        const existing = document.getElementById('product-modal-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'product-modal-overlay';
        overlay.className = 'modal-overlay';

        const imageSrc = product.product_image_filename
            ? `/uploads/${product.product_image_filename}`
            : '/static/haws.jpg';

        const certLinesHtml =
            (product.certifications || []).length > 0
                ? product.certifications.map((c) => buildModalCertLineHtml(c)).join('')
                : '<div class="modal-detail-line modal-detail-line--muted">None</div>';

        const madeWithList = Array.isArray(product.made_with)
            ? product.made_with
            : Array.isArray(product.madeWith)
                ? product.madeWith
                : [];
        const madeWithoutList = Array.isArray(product.made_without)
            ? product.made_without
            : Array.isArray(product.madeWithout)
                ? product.madeWithout
                : [];
        const featuresList = Array.isArray(product.attributes)
            ? product.attributes
            : Array.isArray(product.features)
                ? product.features
                : [];

        function sectionTagLines(tags) {
            const sectionColors = [];
            return tags.map((t) => {
                const c = getColorForTag(t, sectionColors);
                const html = buildModalTagLineHtml(t, c);
                sectionColors.push(c);
                return html;
            }).join('');
        }

        const madeWithLines = madeWithList.length
            ? sectionTagLines(madeWithList)
            : '<div class="modal-detail-line modal-detail-line--muted">None</div>';

        const madeWithoutLines = madeWithoutList.length
            ? sectionTagLines(madeWithoutList)
            : '<div class="modal-detail-line modal-detail-line--muted">None</div>';

        const featuresLines = featuresList.length
            ? sectionTagLines(featuresList)
            : '<div class="modal-detail-line modal-detail-line--muted">None</div>';

        const modalFavClass = isFavorite(product.id) ? ' favorited' : '';
        const modalHeartSvg = isFavorite(product.id) ? '/static/heart-filled.svg' : '/static/heart.svg';
        const overlayBrandSrc = brandLogoUrl(product);
        const modalBrandHref = brandLinkUrl(product);
        const brandAltEsc = escapeHtml(product.brand_name || '');
        let modalBrandInner;
        if (overlayBrandSrc) {
            const srcEsc = escapeHtml(overlayBrandSrc);
            modalBrandInner = modalBrandHref
                ? `<a href="${escapeHtml(modalBrandHref)}" class="modal-brand-logo-link" target="_blank" rel="noopener noreferrer"><img class="modal-brand-img" src="${srcEsc}" alt="${brandAltEsc}"></a>`
                : `<img class="modal-brand-img" src="${srcEsc}" alt="${brandAltEsc}">`;
        } else {
            modalBrandInner = escapeHtml(product.brand_name || '');
        }
        const isAffiliateBuy = !!product.earns_commission && !!product.product_link;
        const modalBuyClass = isAffiliateBuy
            ? 'buy-button buy-button--affiliate header-login-btn modal-buy-button'
            : 'buy-button header-login-btn modal-buy-button';
        const modalPriceNum = Number(product.price);
        const modalBuyInner = Number.isFinite(modalPriceNum)
            ? `$${modalPriceNum.toFixed(2)}`
            : '$0.00';
        const countryRawModal = String(product.made_in || '').trim();
        const descTrim = (product.description || '').trim();
        const modalDescriptionBody = descTrim
            ? `<p class="modal-description">${product.description}</p>`
            : `<p class="modal-description is-empty">No description provided.</p>`;
        const metadataStackHtml =
            '<div class="modal-metadata-stack modal-metadata-stack--flat">' +
            '<div class="modal-metadata-columns">' +
            '<div class="modal-metadata-col modal-metadata-col--left">' +
            buildModalFlatSectionHtml('Made In', buildModalMadeInSectionBodyHtml(countryRawModal), false) +
            buildModalFlatSectionHtml('Certifications', certLinesHtml, false) +
            buildModalFlatSectionHtml('Features', featuresLines, false) +
            '</div>' +
            '<div class="modal-metadata-col modal-metadata-col--right">' +
            buildModalFlatSectionHtml('Made With', madeWithLines, false) +
            buildModalFlatSectionHtml('Made Without', madeWithoutLines, false) +
            '</div>' +
            '</div>' +
            '</div>';
        const productLinkRaw = String(product.product_link || '').trim();
        const productLinkEsc = escapeHtml(productLinkRaw);
        const imageSrcEsc = escapeHtml(imageSrc);
        const modalImageBlock = productLinkRaw
            ? '<a href="' + productLinkEsc + '" class="modal-image-link" target="_blank" rel="noopener noreferrer" aria-label="Open product page (new tab)">' +
            '<img src="' + imageSrcEsc + '" alt="' + escapeHtml(product.name) + '" class="modal-image" onerror="this.src=\'/static/haws.jpg\'">' +
            '</a>'
            : '<img src="' + imageSrcEsc + '" alt="' + escapeHtml(product.name) + '" class="modal-image" onerror="this.src=\'/static/haws.jpg\'">';
        const modalBuyHtml = product.product_link
            ? `<a href="${productLinkEsc}" target="_blank" rel="noopener noreferrer" class="${modalBuyClass}">${modalBuyInner}</a>`
            : `<button type="button" class="${modalBuyClass}">${modalBuyInner}</button>`;
        overlay.innerHTML = `
                <div class="modal-card">
                    <div class="modal-header-top">
                        <div class="modal-header-brand-line">
                            <div class="modal-header-brand-row">
                                <div class="modal-header-brand">
                                    <p class="modal-brand">${modalBrandInner}</p>
                                </div>
                                <div class="modal-header-brand-actions">
                                    ${modalBuyHtml}
                                </div>
                            </div>
                            <div class="modal-header-sub">
                                <button type="button" class="favorite-heart modal-heart-btn${modalFavClass}" id="modal-heart-btn" aria-label="Favorite">
                                    <img src="${modalHeartSvg}" alt="" class="favorite-heart-icon">
                                </button>
                                <span class="modal-category">${buildProductCardCategoryHtml(product)}</span>
                            </div>
                            <div class="modal-header-title-row">
                                <h2 class="modal-product-name modal-product-name--header">${escapeHtml(product.name)}</h2>
                            </div>
                        </div>
                        <div class="modal-header-main">
                            <div class="modal-flat-section-body modal-flat-section-body--prose modal-header-description">
                                ${modalDescriptionBody}
                            </div>
                        </div>
                    </div>
                    <div class="modal-body">
                        <div class="modal-image-col">
                            ${modalImageBlock}
                        </div>
                        <div class="modal-details-col">
                            ${metadataStackHtml}
                        </div>
                    </div>
                </div>
            `;

        overlay.querySelector('#modal-heart-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            onToggleFavorite(product.id);
            const btn = overlay.querySelector('#modal-heart-btn');
            const fav = isFavorite(product.id);
            btn.classList.toggle('favorited', fav);
            btn.querySelector('.favorite-heart-icon').src = fav ? '/static/heart-filled.svg' : '/static/heart.svg';
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });
        document.body.appendChild(overlay);
    }

    global.TabbedCatalogUI = {
        CERT_IMAGE_MAP,
        getFlagUrl,
        tabbedCertName,
        certIconUrl,
        brandLogoUrl,
        brandLinkUrl,
        certLinkUrl,
        primeTagColorsFromProducts,
        getColorForTag,
        createProductCard,
        createArticleProductExpandedEmbed,
        openProductModal
    };
})(typeof window !== 'undefined' ? window : this);
