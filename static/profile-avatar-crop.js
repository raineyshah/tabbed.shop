(function () {
    'use strict';

    var VIEW_SIZE = 280;
    var EXPORT_SIZE = 512;
    var SCALE_MAX = 3;

    var backdrop = document.getElementById('profile-avatar-modal-backdrop');
    var dialog = document.getElementById('profile-avatar-modal');
    var openBtn = document.getElementById('profile-avatar-edit-trigger');
    var closeBtn = document.getElementById('profile-avatar-modal-close');
    var cancelBtn = document.getElementById('profile-avatar-modal-cancel');
    var removeBtn = document.getElementById('profile-avatar-modal-remove');
    var saveBtn = document.getElementById('profile-avatar-modal-save');
    var fileInput = document.getElementById('profile-avatar-file');
    var pickBtn = document.getElementById('profile-avatar-pick-file');
    var canvas = document.getElementById('profile-avatar-preview');
    var zoomInput = document.getElementById('profile-avatar-zoom');
    var statusEl = document.getElementById('profile-avatar-status');

    if (!backdrop || !dialog || !canvas) return;

    var ctx = canvas.getContext('2d');
    canvas.width = VIEW_SIZE;
    canvas.height = VIEW_SIZE;

    var state = {
        img: null,
        naturalW: 0,
        naturalH: 0,
        ox: 0,
        oy: 0,
        scale: 1,
        baseScale: 1,
    };

    /** Smallest scale so both scaled width and height are >= circle diameter (viewport). */
    function getScaleMin() {
        if (!state.naturalW || !state.naturalH) return 1;
        return Math.max(
            VIEW_SIZE / (state.baseScale * state.naturalW),
            VIEW_SIZE / (state.baseScale * state.naturalH),
        );
    }

    /**
     * Keep zoom >= scaleMin and pan so the circle is always fully covered (no empty wedges).
     * The circle is inscribed in a VIEW_SIZE square; the image rect must cover that square.
     */
    function applyCoverConstraints() {
        if (!state.img || !state.naturalW) return;

        var half = VIEW_SIZE / 2;
        var smin = getScaleMin();
        if (state.scale < smin) state.scale = smin;
        if (state.scale > SCALE_MAX) state.scale = SCALE_MAX;

        var bs = state.baseScale * state.scale;
        var dw = state.naturalW * bs;
        var dh = state.naturalH * bs;

        var oxMin = half - dw / 2;
        var oxMax = dw / 2 - half;
        var oyMin = half - dh / 2;
        var oyMax = dh / 2 - half;

        if (state.ox < oxMin) state.ox = oxMin;
        if (state.ox > oxMax) state.ox = oxMax;
        if (state.oy < oyMin) state.oy = oyMin;
        if (state.oy > oyMax) state.oy = oyMax;

        if (zoomInput) {
            zoomInput.min = String(Math.max(1, Math.ceil(smin * 100)));
            zoomInput.max = String(Math.floor(SCALE_MAX * 100));
            zoomInput.value = String(Math.round(state.scale * 100));
        }
    }

    function renderPreview() {
        applyCoverConstraints();
        var cw = VIEW_SIZE;
        var ch = VIEW_SIZE;
        ctx.clearRect(0, 0, cw, ch);
        ctx.save();
        ctx.beginPath();
        ctx.arc(cw / 2, ch / 2, cw / 2, 0, Math.PI * 2);
        ctx.clip();
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cw, ch);
        if (!state.img || !state.naturalW) {
            ctx.restore();
            return;
        }
        var bs = state.baseScale * state.scale;
        var dw = state.naturalW * bs;
        var dh = state.naturalH * bs;
        var cx = cw / 2 + state.ox;
        var cy = ch / 2 + state.oy;
        ctx.drawImage(state.img, cx - dw / 2, cy - dh / 2, dw, dh);
        ctx.restore();
    }

    function resetEditor() {
        state.img = null;
        state.naturalW = 0;
        state.naturalH = 0;
        state.ox = state.oy = 0;
        state.scale = 1;
        state.baseScale = 1;
        if (fileInput) fileInput.value = '';
        if (zoomInput) {
            zoomInput.min = '100';
            zoomInput.max = String(Math.floor(SCALE_MAX * 100));
            zoomInput.value = '100';
        }
        if (statusEl) statusEl.textContent = '';
        renderPreview();
    }

    function exportBlob(cb) {
        applyCoverConstraints();
        var c = document.createElement('canvas');
        c.width = EXPORT_SIZE;
        c.height = EXPORT_SIZE;
        var x = c.getContext('2d');
        var cw = EXPORT_SIZE;
        var ch = EXPORT_SIZE;
        x.fillStyle = '#ffffff';
        x.fillRect(0, 0, cw, ch);
        x.save();
        x.beginPath();
        x.arc(cw / 2, ch / 2, cw / 2, 0, Math.PI * 2);
        x.clip();
        if (!state.img || !state.naturalW) {
            x.restore();
            cb(null);
            return;
        }
        var ratio = EXPORT_SIZE / VIEW_SIZE;
        var bs = state.baseScale * state.scale;
        var dw = state.naturalW * bs * ratio;
        var dh = state.naturalH * bs * ratio;
        var cx = cw / 2 + state.ox * ratio;
        var cy = ch / 2 + state.oy * ratio;
        x.drawImage(state.img, cx - dw / 2, cy - dh / 2, dw, dh);
        x.restore();
        c.toBlob(function (blob) {
            cb(blob);
        }, 'image/jpeg', 0.92);
    }

    function applyPlaceholderAvatarEverywhere() {
        var page = document.getElementById('profile-page-avatar');
        if (page) {
            page.src = '/static/person.svg';
            page.classList.add('profile-user-avatar--default');
            var ring = page.closest('.profile-avatar-ring');
            if (ring) ring.classList.add('profile-avatar-ring--default');
        }
        var land = document.getElementById('profile-avatar-landing');
        if (land) {
            land.src = '/static/person.svg';
            land.classList.add('profile-v2-avatar--placeholder');
            var shell = land.closest('.profile-v2-avatar-shell');
            if (shell) shell.classList.add('profile-v2-avatar-shell--default');
        }
        var header = document.getElementById('header-profile-avatar');
        if (header) {
            header.src = '/static/person.svg';
            header.classList.add('header-profile-avatar-img--placeholder');
            if (typeof window.syncHeaderAvatarPlaceholderClass === 'function') {
                window.syncHeaderAvatarPlaceholderClass(header);
            }
        }
        var mobileAv = document.getElementById('mobile-cat-acct-avatar');
        if (mobileAv) {
            mobileAv.src = '/static/person.svg';
            mobileAv.classList.add('profile-v2-avatar--placeholder');
            var mShell = mobileAv.closest('.profile-v2-avatar-shell');
            if (mShell) mShell.classList.add('profile-v2-avatar-shell--default');
        }
        if (removeBtn) removeBtn.hidden = true;
    }

    function bustAvatarUrls() {
        var t = '?t=' + Date.now();
        var page = document.getElementById('profile-page-avatar');
        if (page) {
            var base = page.src.split('?')[0];
            if (base.indexOf('profile-placeholder.png') !== -1 || base.indexOf('person.svg') !== -1) {
                var u = window.location.pathname.split('/').filter(Boolean);
                var uname = u[0] === 'user' && u[1] ? u[1] : '';
                if (uname) {
                    page.src = '/api/users/' + encodeURIComponent(uname) + '/avatar' + t;
                }
            } else {
                page.src = base + t;
            }
            page.classList.remove('profile-user-avatar--default');
            var ring = page.closest('.profile-avatar-ring');
            if (ring) ring.classList.remove('profile-avatar-ring--default');
        }
        var land = document.getElementById('profile-avatar-landing');
        if (land) {
            var lb = land.src.split('?')[0];
            if (lb.indexOf('profile-placeholder.png') !== -1 || lb.indexOf('person.svg') !== -1) {
                var u2 = window.location.pathname.split('/').filter(Boolean);
                var uname2 = u2[0] === 'user' && u2[1] ? u2[1] : '';
                if (uname2) land.src = '/api/users/' + encodeURIComponent(uname2) + '/avatar' + t;
            } else {
                land.src = lb + t;
            }
            land.classList.remove('profile-v2-avatar--placeholder');
            var shell = land.closest('.profile-v2-avatar-shell');
            if (shell) shell.classList.remove('profile-v2-avatar-shell--default');
        }
        var header = document.getElementById('header-profile-avatar');
        if (header) {
            var hb = header.src.split('?')[0];
            header.src = hb + t;
            if (typeof window.syncHeaderAvatarPlaceholderClass === 'function') {
                window.syncHeaderAvatarPlaceholderClass(header);
            }
        }
    }

    function commitImageToEditorState(img) {
        state.img = img;
        state.naturalW = img.naturalWidth;
        state.naturalH = img.naturalHeight;
        if (!state.naturalW || !state.naturalH) {
            if (statusEl) statusEl.textContent = 'Could not read image dimensions.';
            state.img = null;
            state.naturalW = 0;
            state.naturalH = 0;
            renderPreview();
            return;
        }
        state.baseScale = Math.max(
            VIEW_SIZE / state.naturalW,
            VIEW_SIZE / state.naturalH,
        );
        state.scale = 1;
        state.ox = 0;
        state.oy = 0;
        renderPreview();
        if (statusEl) {
            statusEl.textContent =
                'Drag to reposition. Zoom cannot go below filling the circle.';
        }
    }

    /** Username in path: /user/{name} or /user/{name}/settings */
    function profileUsernameFromPath() {
        var parts = window.location.pathname.split('/').filter(Boolean);
        if (parts[0] === 'user' && parts[1]) {
            try {
                return decodeURIComponent(parts[1]);
            } catch (e) {
                return parts[1];
            }
        }
        return '';
    }

    /** Best URL for the avatar already on the page (v2 hero uses #profile-avatar-landing). */
    function currentAvatarUrlForEditor() {
        var land = document.getElementById('profile-avatar-landing');
        var page = document.getElementById('profile-page-avatar');
        function looksLikePlaceholder(base) {
            return !base || /person\.svg/i.test(base) || /profile-placeholder/i.test(base);
        }
        function baseFromImg(el) {
            if (!el) return '';
            var src = el.getAttribute('src') || el.src || '';
            if (!src) return '';
            return src.split('#')[0].split('?')[0];
        }
        var bLand = baseFromImg(land);
        if (bLand && !looksLikePlaceholder(bLand)) return bLand;
        var bPage = baseFromImg(page);
        if (bPage && !looksLikePlaceholder(bPage)) return bPage;
        if (removeBtn && !removeBtn.hidden) {
            var u = profileUsernameFromPath();
            if (u) return '/api/users/' + encodeURIComponent(u) + '/avatar';
        }
        return '';
    }

    function loadImageFromUrl(url) {
        if (!url) return;
        var img = new Image();
        img.onload = function () {
            commitImageToEditorState(img);
        };
        img.onerror = function () {
            if (statusEl) statusEl.textContent = 'Could not load your current photo.';
        };
        var sep = url.indexOf('?') >= 0 ? '&' : '?';
        img.src = url + sep + 't=' + Date.now();
    }

    function loadCurrentAvatarIntoEditor() {
        var url = currentAvatarUrlForEditor();
        if (!url) return;
        if (statusEl) statusEl.textContent = 'Loading your photo…';
        loadImageFromUrl(url);
    }

    function openModal() {
        resetEditor();
        backdrop.hidden = false;
        dialog.hidden = false;
        backdrop.setAttribute('aria-hidden', 'false');
        loadCurrentAvatarIntoEditor();
        if (pickBtn) pickBtn.focus();
    }

    function closeModal() {
        backdrop.hidden = true;
        dialog.hidden = true;
        backdrop.setAttribute('aria-hidden', 'true');
        resetEditor();
        if (openBtn) openBtn.focus();
    }

    if (openBtn) {
        openBtn.addEventListener('click', function () {
            openModal();
        });
    }

    var pageAvatar = document.getElementById('profile-page-avatar');
    var pencilOnly = openBtn && openBtn.classList && openBtn.classList.contains('profile-avatar-pencil');
    if (pageAvatar && openBtn && !pencilOnly) {
        pageAvatar.addEventListener('click', function () {
            openModal();
        });
    }

    function closersClick(e) {
        e.preventDefault();
        closeModal();
    }

    if (closeBtn) closeBtn.addEventListener('click', closersClick);
    if (cancelBtn) cancelBtn.addEventListener('click', closersClick);

    if (removeBtn) {
        removeBtn.addEventListener('click', function () {
            if (!window.confirm('Remove your profile photo?')) return;
            removeBtn.disabled = true;
            if (statusEl) statusEl.textContent = 'Removing…';
            fetch('/api/me/avatar', {
                method: 'DELETE',
                credentials: 'same-origin',
            })
                .then(function (r) {
                    if (!r.ok) {
                        return r.json().then(function (j) {
                            var d = j && j.detail;
                            if (typeof d === 'string') throw new Error(d);
                            if (Array.isArray(d) && d[0] && d[0].msg) throw new Error(d[0].msg);
                            throw new Error('Could not remove photo');
                        }, function () {
                            throw new Error('Could not remove photo');
                        });
                    }
                    return r.json();
                })
                .then(function () {
                    applyPlaceholderAvatarEverywhere();
                    closeModal();
                })
                .catch(function (err) {
                    if (statusEl) statusEl.textContent = err.message || 'Could not remove photo.';
                })
                .finally(function () {
                    removeBtn.disabled = false;
                });
        });
    }

    backdrop.addEventListener('click', function (e) {
        if (e.target === backdrop) closeModal();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (backdrop.hidden) return;
        closeModal();
    });

    if (pickBtn && fileInput) {
        pickBtn.addEventListener('click', function () {
            fileInput.click();
        });
    }

    function loadImageFromFile(f) {
        if (!f) return;
        if (f.type && !/^image\//.test(f.type)) {
            if (statusEl) statusEl.textContent = 'Choose an image file.';
            return;
        }
        var reader = new FileReader();
        reader.onerror = function () {
            if (statusEl) statusEl.textContent = 'Could not read that file.';
        };
        reader.onload = function () {
            var dataUrl = reader.result;
            var img = new Image();
            img.onload = function () {
                commitImageToEditorState(img);
            };
            img.onerror = function () {
                if (statusEl) statusEl.textContent = 'Could not load that image.';
            };
            img.src = dataUrl;
        };
        reader.readAsDataURL(f);
    }

    if (fileInput) {
        fileInput.addEventListener('change', function () {
            var f = fileInput.files && fileInput.files[0];
            if (!f) return;
            loadImageFromFile(f);
        });
    }

    if (zoomInput) {
        zoomInput.addEventListener('input', function () {
            var pct = Number(zoomInput.value) || 100;
            state.scale = pct / 100;
            renderPreview();
        });
    }

    var dragging = false;
    var lastX = 0;
    var lastY = 0;

    canvas.addEventListener('pointerdown', function (e) {
        if (!state.img) return;
        dragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        try {
            canvas.setPointerCapture(e.pointerId);
        } catch (err) {}
    });

    canvas.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        state.ox += e.clientX - lastX;
        state.oy += e.clientY - lastY;
        lastX = e.clientX;
        lastY = e.clientY;
        renderPreview();
    });

    function endDrag(e) {
        dragging = false;
        try {
            if (e.pointerId != null) canvas.releasePointerCapture(e.pointerId);
        } catch (err) {}
    }

    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);

    canvas.addEventListener(
        'wheel',
        function (e) {
            if (!state.img) return;
            e.preventDefault();
            var delta = e.deltaY > 0 ? -0.06 : 0.06;
            state.scale = state.scale + delta;
            renderPreview();
        },
        { passive: false },
    );

    if (saveBtn) {
        saveBtn.addEventListener('click', function () {
            if (!state.img) {
                if (statusEl) statusEl.textContent = 'Choose a photo first.';
                return;
            }
            saveBtn.disabled = true;
            if (statusEl) statusEl.textContent = 'Saving…';
            exportBlob(function (blob) {
                if (!blob) {
                    if (statusEl) statusEl.textContent = 'Could not create image.';
                    saveBtn.disabled = false;
                    return;
                }
                var fd = new FormData();
                fd.append('avatar', blob, 'avatar.jpg');
                fetch('/api/me/avatar', {
                    method: 'POST',
                    body: fd,
                    credentials: 'same-origin',
                })
                    .then(function (r) {
                        if (!r.ok) {
                            return r.json().then(function (j) {
                                var d = j && j.detail;
                                if (typeof d === 'string') throw new Error(d);
                                if (Array.isArray(d) && d[0] && d[0].msg) throw new Error(d[0].msg);
                                throw new Error('Upload failed');
                            }, function () {
                                throw new Error('Upload failed');
                            });
                        }
                        return r.json();
                    })
                    .then(function () {
                        bustAvatarUrls();
                        if (removeBtn) removeBtn.hidden = false;
                        closeModal();
                    })
                    .catch(function (err) {
                        if (statusEl) statusEl.textContent = err.message || 'Upload failed.';
                    })
                    .finally(function () {
                        saveBtn.disabled = false;
                    });
            });
        });
    }
})();
