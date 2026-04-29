/**
 * Unified admin: Products (wide SQLite-column table + chip pickers), Brands & Certifications (sheet).
 * Entity: body[data-admin-entity] or path (/admin/products | /admin/brands | /admin/certifications).
 */
(function () {
    var root = document.getElementById('admin-unified-panel');
    if (!root) return;

    var statusEl = document.getElementById('sheetStatus');
    var mountProducts = document.getElementById('admin-sheet-product-mount');
    var tableWrap = document.getElementById('admin-sheet-table-wrap');
    var thead = document.getElementById('adminSheetThead');
    var tbody = document.getElementById('adminSheetTbody');
    var introEl = document.getElementById('adminUnifiedIntro');
    var aiBtn = document.getElementById('sheetBtnAiFill');

    var sheetApi = {
        reload: function () {},
        addRow: function () {},
        deleteSelected: function () {},
        duplicateSelected: function () {},
        copy: function () {},
        paste: function () {},
        fillDown: function () {},
        fillRight: function () {},
        saveAll: function () {},
        aiFill: function () {},
    };

    function setStatus(msg, isErr) {
        if (!statusEl) return;
        statusEl.textContent = msg || '';
        statusEl.style.color = isErr ? '#b91c1c' : '';
    }

    function toast(msg, isErr) {
        setStatus(msg, isErr);
        if (typeof window.tabbedShowGlobalToast === 'function') {
            window.tabbedShowGlobalToast(msg, { isError: !!isErr });
        }
    }

    function normalizeEntity(v) {
        var s = String(v || '')
            .trim()
            .toLowerCase();
        if (s === 'brands' || s === 'certifications' || s === 'products') return s;
        return 'products';
    }

    function readEntity() {
        var b = normalizeEntity(document.body.getAttribute('data-admin-entity'));
        if (b === 'brands' || b === 'certifications' || b === 'products') return b;
        var path = (location.pathname || '').replace(/\/+$/, '');
        if (path.endsWith('/admin/brands')) return 'brands';
        if (path.endsWith('/admin/certifications')) return 'certifications';
        if (path.endsWith('/admin/products')) return 'products';
        return 'products';
    }

    function updateEntityTabs(entity) {
        document.body.setAttribute('data-admin-entity', entity);
        if (introEl) {
            if (entity === 'brands' || entity === 'certifications') {
                introEl.hidden = false;
                introEl.textContent = entity === 'brands'
                    ? 'Spreadsheet: select cells, copy/paste TSV, fill down/right. New rows need a logo file on first save.'
                    : 'Spreadsheet: select cells, copy/paste TSV. New rows need a badge image on first save.';
            } else {
                introEl.hidden = true;
                introEl.textContent = '';
            }
        }
        var isProducts = entity === 'products';
        if (mountProducts) mountProducts.hidden = !isProducts;
        if (tableWrap) tableWrap.hidden = isProducts;
        if (aiBtn) aiBtn.hidden = !isProducts;
        var aiSep = document.querySelector('.admin-sheet-ai-sep');
        if (aiSep) aiSep.hidden = !isProducts;
        var addProductBtn = document.getElementById('sheetBtnAddProduct');
        var duplicateBtn = document.getElementById('sheetBtnDuplicateRow');
        if (addProductBtn) addProductBtn.hidden = !isProducts;
        if (duplicateBtn) duplicateBtn.hidden = !isProducts;
    }

    function wireToolbarOnce() {
        function bind(id, key) {
            var el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('click', function () {
                var fn = sheetApi[key];
                if (typeof fn === 'function') fn();
            });
        }
        bind('sheetBtnReload', 'reload');
        bind('sheetBtnAddRow', 'addRow');
        bind('sheetBtnDeleteRows', 'deleteSelected');
        bind('sheetBtnDuplicateRow', 'duplicateSelected');
        bind('sheetBtnSaveAll', 'saveAll');
        bind('sheetBtnAiFill', 'aiFill');
    }

    /* ---------- Products (SQLite-column table) ---------- */
    function mountProductsSheet() {
        var productTbody = document.getElementById('adminProductSheetTbody');
        var tpl = document.getElementById('admin-sheet-product-row-tpl');
        var boot = window.tabbedBootAdminProductForm;
        if (!mountProducts || !productTbody || !tpl || typeof boot !== 'function') {
            if (productTbody) {
                productTbody.innerHTML =
                    '<tr><td colspan="19" class="admin-product-sheet-err"><p class="alert alert-error">Missing product row template or admin-product-create.js.</p></td></tr>';
            }
            sheetApi = {
                reload: function () {},
                addRow: function () {},
                deleteSelected: function () {},
                copy: function () {},
                paste: function () {},
                fillDown: function () {},
                fillRight: function () {},
                saveAll: function () {},
                aiFill: function () {},
            };
            return function () {
                if (productTbody) productTbody.innerHTML = '';
            };
        }

        var nextRowIndex = 0;

        function buildRow(apiProduct) {
            var ri = nextRowIndex++;
            var prefix = 'sr' + ri + '-';
            var html = tpl.innerHTML.replace(/__RP__/g, prefix);
            /* Parsing a lone <tr> with DOMParser often drops it (invalid under <body>). Use <tbody>. */
            var wrap = document.createElement('tbody');
            wrap.innerHTML = html.trim();
            var tr = wrap.querySelector('tr[data-admin-product-row]');
            if (!tr) return;
            tr.dataset.productId =
                apiProduct && apiProduct.id != null ? String(apiProduct.id) : '';

            var idRead = tr.querySelector('.admin-product-sheet-id-readonly');
            var fnRead = tr.querySelector('.admin-product-sheet-filename-readonly');
            if (idRead) {
                idRead.textContent =
                    apiProduct && apiProduct.id != null ? String(apiProduct.id) : '—';
            }
            if (fnRead) {
                fnRead.textContent =
                    apiProduct && apiProduct.product_image_filename
                        ? String(apiProduct.product_image_filename)
                        : '—';
            }

            productTbody.appendChild(tr);

            var formId = prefix + 'sheetProductForm';
            boot(prefix, {
                mode: 'add',
                formId: formId,
                alertsId: prefix + 'sheet-alerts',
                sheetMode: true,
                getProductId: function () {
                    var raw = tr.dataset.productId || '';
                    if (!raw) return null;
                    var n = parseInt(raw, 10);
                    return isNaN(n) ? null : n;
                },
            });

            var form = document.getElementById(formId);
            if (form && typeof form.tabbedAdminPopulate === 'function') {
                form.tabbedAdminPopulate(
                    apiProduct || {
                        name: '',
                        made_with: [],
                        made_without: [],
                        attributes: [],
                        certifications: [],
                    }
                );
            }

            tr.querySelector('.admin-sheet-card-save').addEventListener('click', function () {
                if (!form || typeof form.tabbedAdminSave !== 'function') return;
                form.tabbedAdminSave().then(function (res) {
                    if (res && res.ok && res.data && res.data.product) {
                        var p = res.data.product;
                        if (p.id != null) {
                            tr.dataset.productId = String(p.id);
                            if (idRead) idRead.textContent = String(p.id);
                        }
                        if (fnRead && p.product_image_filename) {
                            fnRead.textContent = String(p.product_image_filename);
                        }
                    }
                });
            });

            tr.querySelector('.admin-sheet-card-delete').addEventListener('click', function () {
                var id = tr.dataset.productId ? parseInt(tr.dataset.productId, 10) : NaN;
                if (!id || isNaN(id)) {
                    tr.remove();
                    toast('Removed unsaved row.');
                    return;
                }
                if (!window.confirm('Delete product #' + id + '?')) return;
                fetch('/api/admin/products/' + encodeURIComponent(id), {
                    method: 'DELETE',
                    credentials: 'same-origin',
                })
                    .then(function (r) {
                        if (r.ok) {
                            tr.remove();
                            toast('Deleted.');
                        } else toast('Delete failed.', true);
                    })
                    .catch(function () {
                        toast('Delete failed.', true);
                    });
            });
        }

        function loadAll() {
            productTbody.innerHTML = '';
            nextRowIndex = 0;
            return fetch('/api/admin/products', { credentials: 'same-origin', cache: 'no-store' })
                .then(function (res) {
                    if (!res.ok) throw new Error('Could not load products.');
                    return res.json();
                })
                .then(function (data) {
                    var warm =
                        typeof window.tabbedAdminWarmReferenceCatalogs === 'function'
                            ? window.tabbedAdminWarmReferenceCatalogs()
                            : typeof window.__tabbedAdminRefreshReferencePickers === 'function'
                              ? window.__tabbedAdminRefreshReferencePickers()
                              : Promise.resolve();
                    return warm.then(function () {
                        var list = data.products || [];
                        for (var i = 0; i < list.length; i++) buildRow(list[i]);
                        setStatus('Loaded ' + list.length + ' product(s).');
                    });
                });
        }

        sheetApi.reload = function () {
            loadAll().catch(function (e) {
                toast(e.message || String(e), true);
            });
        };
        sheetApi.addRow = function () {
            buildRow(null);
            toast('Added product row.');
        };
        sheetApi.duplicateSelected = function () {
            var boxes = productTbody.querySelectorAll('.admin-sheet-row-bulk:checked');
            if (!boxes.length) {
                toast('Select rows to duplicate using the checkbox in the first column.', true);
                return;
            }
            var pending = 0;
            var done = 0;
            for (var i = 0; i < boxes.length; i++) {
                var row = boxes[i].closest('[data-admin-product-row]');
                if (!row) continue;
                var pid = row.dataset.productId ? parseInt(row.dataset.productId, 10) : NaN;
                if (!pid || isNaN(pid)) {
                    buildRow(null);
                    done++;
                    continue;
                }
                pending++;
                (function (id) {
                    fetch('/api/admin/products/' + encodeURIComponent(id), {
                        credentials: 'same-origin',
                        cache: 'no-store',
                    })
                        .then(function (r) {
                            if (!r.ok) throw new Error('Could not fetch product #' + id);
                            return r.json();
                        })
                        .then(function (p) {
                            var copy = Object.assign({}, p);
                            delete copy.id;
                            copy.product_image_filename = null;
                            buildRow(copy);
                            done++;
                            pending--;
                            if (!pending) toast('Duplicated ' + done + ' row(s). Edit and save to create new products.');
                        })
                        .catch(function (e) {
                            pending--;
                            toast(e.message || 'Duplicate failed.', true);
                        });
                })(pid);
            }
            if (!pending && done) toast('Duplicated ' + done + ' row(s). Edit and save to create new products.');
        };
        sheetApi.deleteSelected = function () {
            var boxes = productTbody.querySelectorAll('.admin-sheet-row-bulk:checked');
            if (!boxes.length) {
                toast('Select rows with the checkbox in the first column.', true);
                return;
            }
            var rows = [];
            for (var i = 0; i < boxes.length; i++) {
                rows.push(boxes[i].closest('[data-admin-product-row]'));
            }
            var ids = [];
            var unsaved = 0;
            for (var j = 0; j < rows.length; j++) {
                var c = rows[j];
                if (!c) continue;
                var pid = c.dataset.productId ? parseInt(c.dataset.productId, 10) : NaN;
                if (pid && !isNaN(pid)) ids.push(pid);
                else unsaved++;
            }
            if (
                !window.confirm(
                    'Delete ' + ids.length + ' saved product(s) and remove ' + unsaved + ' new row(s)?'
                )
            ) {
                return;
            }
            for (var u = 0; u < rows.length; u++) {
                var cx = rows[u];
                if (!cx) continue;
                var p2 = cx.dataset.productId ? parseInt(cx.dataset.productId, 10) : NaN;
                if (!p2 || isNaN(p2)) cx.remove();
            }
            if (!ids.length) {
                toast('Removed new rows.');
                return;
            }
            fetch('/api/admin/products/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ ids: ids }),
            })
                .then(function (res) {
                    if (!res.ok) {
                        toast('Bulk delete failed.', true);
                        return Promise.reject();
                    }
                    return loadAll();
                })
                .then(function () {
                    toast('Deleted selected.');
                })
                .catch(function () {});
        };
        sheetApi.copy = function () {};
        sheetApi.paste = function () {};
        sheetApi.fillDown = function () {};
        sheetApi.fillRight = function () {};
        sheetApi.saveAll = function () {
            var forms = productTbody.querySelectorAll('form.admin-product-form');
            var promises = [];
            for (var i = 0; i < forms.length; i++) {
                (function (f) {
                    if (f.dataset.dirty !== '1' || typeof f.tabbedAdminSave !== 'function') return;
                    promises.push(
                        f.tabbedAdminSave().then(function (res) {
                            if (res && res.ok && res.data && res.data.product) {
                                var row = f.closest('[data-admin-product-row]');
                                var p = res.data.product;
                                if (row && p.id != null) {
                                    row.dataset.productId = String(p.id);
                                    var idSp = row.querySelector('.admin-product-sheet-id-readonly');
                                    if (idSp) idSp.textContent = String(p.id);
                                    var fnSp = row.querySelector('.admin-product-sheet-filename-readonly');
                                    if (fnSp && p.product_image_filename) {
                                        fnSp.textContent = String(p.product_image_filename);
                                    }
                                }
                            }
                            return res;
                        })
                    );
                })(forms[i]);
            }
            if (!promises.length) {
                toast('No changed forms (edit a field first).');
                return;
            }
            Promise.all(promises)
                .then(function () {
                    toast('Saved ' + promises.length + ' form(s).');
                })
                .catch(function () {
                    toast('Some saves failed.', true);
                });
        };
        sheetApi.aiFill = function () {
            var boxes = productTbody.querySelectorAll('.admin-sheet-row-bulk:checked');
            if (!boxes.length) {
                toast('Select rows first, or use AI on each row’s link cell.', true);
                return;
            }
            var run = function (idx) {
                if (idx >= boxes.length) {
                    toast('AI populate finished for selection.');
                    return;
                }
                var row = boxes[idx].closest('[data-admin-product-row]');
                var b = row && row.querySelector('.admin-product-link-ai-btn');
                if (b) b.click();
                setTimeout(function () {
                    run(idx + 1);
                }, 350);
            };
            run(0);
        };

        sheetApi.reload();

        return function teardown() {
            if (productTbody) productTbody.innerHTML = '';
        };
    }

    /* ---------- Brands / Certifications table ---------- */
    function mountTableSheet(kind) {
        var rows = [];
        var suppressReload = false;

        function newRow() {
            return { id: null, name: '', link: '', has_image: false, imageFile: null, _dirty: true };
        }

        function renderHeader() {
            var cells = ['Sel', 'ID', 'Name', 'Link', 'Image', 'Actions'];
            var tr = document.createElement('tr');
            cells.forEach(function (t) {
                var th = document.createElement('th');
                th.textContent = t;
                tr.appendChild(th);
            });
            thead.innerHTML = '';
            thead.appendChild(tr);
        }

        function renderBody() {
            tbody.innerHTML = '';
            for (var ri = 0; ri < rows.length; ri++) {
                (function (ri) {
                    var row = rows[ri];
                    var tr = document.createElement('tr');

                    /* Checkbox */
                    var tdSel = document.createElement('td');
                    tdSel.className = 'admin-sheet-td admin-sheet-td--narrow';
                    var chk = document.createElement('input');
                    chk.type = 'checkbox';
                    chk.className = 'admin-sheet-row-bulk';
                    chk.setAttribute('aria-label', 'Select row');
                    tdSel.appendChild(chk);
                    tr.appendChild(tdSel);

                    /* ID (readonly) */
                    var tdId = document.createElement('td');
                    tdId.className = 'admin-sheet-td admin-sheet-td--narrow';
                    var idSpan = document.createElement('span');
                    idSpan.className = 'admin-product-sheet-id-readonly text-muted-xs';
                    idSpan.textContent = row.id != null ? String(row.id) : '—';
                    tdId.appendChild(idSpan);
                    tr.appendChild(tdId);

                    /* Name */
                    var tdName = document.createElement('td');
                    tdName.className = 'admin-sheet-td';
                    var nameInput = document.createElement('input');
                    nameInput.type = 'text';
                    nameInput.className = 'admin-sheet-input';
                    nameInput.value = row.name;
                    nameInput.placeholder = 'Name…';
                    nameInput.addEventListener('input', function () {
                        row.name = nameInput.value;
                        row._dirty = true;
                    });
                    tdName.appendChild(nameInput);
                    tr.appendChild(tdName);

                    /* Link */
                    var tdLink = document.createElement('td');
                    tdLink.className = 'admin-sheet-td';
                    var linkInput = document.createElement('input');
                    linkInput.type = 'url';
                    linkInput.className = 'admin-sheet-input';
                    linkInput.value = row.link;
                    linkInput.placeholder = 'https://…';
                    linkInput.addEventListener('input', function () {
                        row.link = linkInput.value;
                        row._dirty = true;
                    });
                    tdLink.appendChild(linkInput);
                    tr.appendChild(tdLink);

                    /* Image */
                    var tdImg = document.createElement('td');
                    tdImg.className = 'admin-sheet-td admin-sheet-td--image';
                    var imgWrap = document.createElement('div');
                    imgWrap.className = 'admin-sheet-image-cell';
                    var img = document.createElement('img');
                    img.className = 'admin-sheet-thumb';
                    img.alt = '';
                    if (row.imageFile) {
                        img.src = URL.createObjectURL(row.imageFile);
                    } else if (row.id && row.has_image) {
                        img.src = kind === 'brands'
                            ? '/api/admin/reference/brands/' + encodeURIComponent(row.id) + '/image?v=' + row.id
                            : '/api/admin/reference/certifications/' + encodeURIComponent(row.id) + '/image?v=' + row.id;
                    } else {
                        img.classList.add('admin-sheet-thumb--empty');
                    }
                    var fi = document.createElement('input');
                    fi.type = 'file';
                    fi.className = 'admin-sheet-file';
                    fi.accept = 'image/jpeg,image/png,image/webp,image/gif,image/svg+xml';
                    fi.addEventListener('change', function () {
                        row.imageFile = fi.files[0] || null;
                        row._dirty = true;
                        if (img.src && img.src.indexOf('blob:') === 0) URL.revokeObjectURL(img.src);
                        if (row.imageFile) {
                            img.src = URL.createObjectURL(row.imageFile);
                            img.classList.remove('admin-sheet-thumb--empty');
                        }
                    });
                    imgWrap.appendChild(img);
                    imgWrap.appendChild(fi);
                    tdImg.appendChild(imgWrap);
                    tr.appendChild(tdImg);

                    /* Actions */
                    var tdAct = document.createElement('td');
                    tdAct.className = 'admin-sheet-td admin-sheet-td--actions';
                    var saveBtn = document.createElement('button');
                    saveBtn.type = 'button';
                    saveBtn.className = 'header-login-btn admin-sheet-card-save';
                    saveBtn.textContent = 'Save';
                    saveBtn.addEventListener('click', function () {
                        saveBrandOrCert(ri).catch(function (e) { toast(e.message || String(e), true); });
                    });
                    var delBtn = document.createElement('button');
                    delBtn.type = 'button';
                    delBtn.className = 'admin-ref-row-delete admin-sheet-card-delete';
                    delBtn.textContent = 'Delete';
                    delBtn.addEventListener('click', function () {
                        deleteRowAt(ri).catch(function (e) { toast(e.message || String(e), true); });
                    });
                    tdAct.appendChild(saveBtn);
                    tdAct.appendChild(delBtn);
                    tr.appendChild(tdAct);

                    tbody.appendChild(tr);
                })(ri);
            }
        }

        async function load() {
            var url = kind === 'brands' ? '/api/admin/reference/brands' : '/api/admin/reference/certifications';
            var res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
            if (!res.ok) throw new Error('Could not load rows.');
            var data = await res.json();
            var key = kind === 'brands' ? 'brands' : 'certifications';
            rows = (data[key] || []).map(function (b) {
                return { id: b.id, name: b.name || '', link: b.link || '', has_image: !!b.has_image, imageFile: null, _dirty: false };
            });
            renderHeader();
            renderBody();
            setStatus('Loaded ' + rows.length + ' row(s).');
        }

        async function saveBrandOrCert(ri) {
            var row = rows[ri];
            var isBrand = kind === 'brands';
            var base = isBrand ? '/api/admin/brands' : '/api/admin/certifications';
            if (!row.name.trim()) throw new Error('Name is required.');
            if (!row.link.trim()) throw new Error('Link is required.');
            var fd = new FormData();
            fd.append('name', row.name.trim());
            fd.append('link', row.link.trim());
            if (row.imageFile) fd.append('image', row.imageFile);
            var res;
            if (row.id) {
                if (!row.has_image && !row.imageFile) throw new Error('Upload an image — this row has no image yet.');
                res = await fetch(base + '/' + encodeURIComponent(row.id), { method: 'POST', body: fd, credentials: 'same-origin' });
            } else {
                if (!row.imageFile) throw new Error('Image is required for new ' + (isBrand ? 'brand' : 'certification') + '.');
                res = await fetch(base, { method: 'POST', body: fd, credentials: 'same-origin' });
            }
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                var d = data.detail;
                throw new Error(typeof d === 'string' ? d : JSON.stringify(d) || 'Save failed');
            }
            row._dirty = false;
            row.imageFile = null;
            if (data.id) row.id = data.id;
            row.has_image = true;
            toast('Saved.');
            if (!suppressReload) await load();
        }

        async function deleteRowAt(ri) {
            var row = rows[ri];
            if (!row.id) {
                rows.splice(ri, 1);
                renderBody();
                toast('Removed unsaved row.');
                return;
            }
            if (!window.confirm('Delete this row permanently?')) return;
            var url = kind === 'brands'
                ? '/api/admin/brands/' + encodeURIComponent(row.id)
                : '/api/admin/certifications/' + encodeURIComponent(row.id);
            var res = await fetch(url, { method: 'DELETE', credentials: 'same-origin' });
            var data = await res.json().catch(function () { return {}; });
            if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Delete failed');
            toast('Deleted.');
            await load();
        }

        async function deleteSelectedRows() {
            var checked = tbody.querySelectorAll('.admin-sheet-row-bulk:checked');
            if (!checked.length) { toast('Select rows with the checkbox first.', true); return; }
            var toDelete = [];
            checked.forEach(function (chk) {
                var tr = chk.closest('tr');
                if (!tr) return;
                var idx = Array.prototype.indexOf.call(tbody.children, tr);
                if (idx >= 0 && rows[idx]) toDelete.push(idx);
            });
            var ids = toDelete.filter(function (i) { return rows[i].id; }).map(function (i) { return rows[i].id; });
            var unsaved = toDelete.filter(function (i) { return !rows[i].id; }).length;
            if (!window.confirm('Delete ' + ids.length + ' saved row(s) and discard ' + unsaved + ' unsaved row(s)?')) return;
            for (var m = 0; m < ids.length; m++) {
                var delUrl = kind === 'brands'
                    ? '/api/admin/brands/' + encodeURIComponent(ids[m])
                    : '/api/admin/certifications/' + encodeURIComponent(ids[m]);
                var r2 = await fetch(delUrl, { method: 'DELETE', credentials: 'same-origin' });
                if (!r2.ok) {
                    var err = await r2.json().catch(function () { return {}; });
                    throw new Error(err.detail || 'Delete failed');
                }
            }
            toast('Deleted selected.');
            await load();
        }

        async function saveAllChanged() {
            var pending = [];
            for (var i = 0; i < rows.length; i++) { if (rows[i]._dirty) pending.push(i); }
            if (!pending.length) { toast('No changed rows.'); return; }
            suppressReload = true;
            try {
                for (var j = 0; j < pending.length; j++) await saveBrandOrCert(pending[j]);
                await load();
                toast('Saved ' + pending.length + ' row(s).');
            } catch (e) {
                toast(e.message || String(e), true);
                try { await load(); } catch (e2) {}
            } finally {
                suppressReload = false;
            }
        }

        sheetApi.reload = function () { load().catch(function (e) { toast(e.message || String(e), true); }); };
        sheetApi.addRow = function () { rows.push(newRow()); renderBody(); toast('Added row at bottom.'); };
        sheetApi.deleteSelected = function () { deleteSelectedRows().catch(function (e) { toast(e.message || String(e), true); }); };
        sheetApi.copy = function () {};
        sheetApi.paste = function () {};
        sheetApi.fillDown = function () {};
        sheetApi.fillRight = function () {};
        sheetApi.saveAll = function () { saveAllChanged().catch(function (e) { toast(e.message || String(e), true); }); };
        sheetApi.aiFill = function () {};

        load().catch(function (e) { toast(e.message || String(e), true); });

        return function teardown() {
            if (thead) thead.innerHTML = '';
            if (tbody) tbody.innerHTML = '';
        };
    }

    var teardownFn = null;

    function mountCurrent(entity) {
        if (teardownFn) {
            teardownFn();
            teardownFn = null;
        }
        if (entity === 'products') teardownFn = mountProductsSheet();
        else if (entity === 'brands' || entity === 'certifications') teardownFn = mountTableSheet(entity);
    }

    function applyEntity(entity) {
        entity = normalizeEntity(entity);
        updateEntityTabs(entity);
        mountCurrent(entity);
    }

    wireToolbarOnce();

    window.tabbedAdminSetEntity = function (e) {
        applyEntity(normalizeEntity(e));
    };

    applyEntity(readEntity());
})();
