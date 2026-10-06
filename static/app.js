// Site-wide "no reload" navigation. Links and forms are fetched in the background and only
// <header>/<main>/<title> are swapped, with a progress bar, page transitions and toasts.
// Everything degrades to normal page loads: if anything here fails, the browser just navigates.
(function () {
    'use strict';

    var seq = 0, controller = null, bar = null, toasts = null;
    var reduceMotion = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

    // ---- init registry: widgets register once and are re-run on every swapped-in <main> ----
    var App = window.App = {
        _inits: [],
        init: function (fn) { this._inits.push(fn); fn(document); },
        run: function (root) { this._inits.forEach(function (fn) { fn(root); }); },
        go: function (url) { navigate(url, {}); },
        toast: toast
    };

    // ---- progress bar ----
    function ensureBar() {
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'progress';
            bar.setAttribute('aria-hidden', 'true');
            bar.appendChild(document.createElement('i'));
            document.body.appendChild(bar);
        }
        return bar;
    }
    function start() {
        var b = ensureBar();
        clearTimeout(b._t);
        b.className = 'progress';
        void b.offsetWidth;                       // restart the transition
        b.classList.add('run');
        document.documentElement.classList.add('is-loading');
    }
    function finish() {
        var b = ensureBar();
        b.classList.add('done');
        document.documentElement.classList.remove('is-loading');
        b._t = setTimeout(function () { b.className = 'progress'; }, 450);
    }

    // ---- toasts (flash messages become these once JS is running) ----
    function toast(text, kind) {
        if (!toasts) {
            toasts = document.createElement('div');
            toasts.className = 'toasts';
            toasts.setAttribute('role', 'status');
            toasts.setAttribute('aria-live', 'polite');
            document.body.appendChild(toasts);
        }
        var t = document.createElement('div');
        t.className = 'toast' + (kind ? ' ' + kind : '');
        t.textContent = text;
        t.addEventListener('click', function () { dismiss(t); });
        toasts.appendChild(t);
        setTimeout(function () { dismiss(t); }, 5000);
    }
    function dismiss(t) {
        if (!t.parentNode || t.classList.contains('out')) return;
        t.classList.add('out');
        setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 250);
    }
    App.init(function (root) {
        root.querySelectorAll('.flash').forEach(function (el) {
            toast(el.textContent.trim());
            el.parentNode.removeChild(el);
        });
    });

    // ---- navigation ----
    function sameDoc(a, b) { return a.pathname === b.pathname && a.search === b.search; }
    function fallback(url) { location.href = url; }

    function navigate(url, opts) {
        var mine = ++seq;
        if (controller) controller.abort();
        controller = new AbortController();
        start();

        var init = { credentials: 'same-origin', signal: controller.signal, headers: { 'X-Requested-With': 'nav' } };
        if (opts.body) {
            init.method = 'POST';
            init.body = opts.body;
            init.headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
        }
        var wait = new Promise(function (r) { setTimeout(r, opts.delay || 0); });   // lets exit animations play

        Promise.all([fetch(url, init), wait]).then(function (res) {
            var r = res[0];
            var type = (r.headers && r.headers.get('content-type')) || '';
            if (!/text\/html/.test(type)) throw { fallback: true, status: r.status };
            return r.text().then(function (html) { return { r: r, html: html }; });
        }).then(function (x) {
            if (mine !== seq) return;
            if (!x.r.ok) throw { fallback: true, status: x.r.status };
            swap(x.html, x.r.url || url, opts);
            finish();
        }).catch(function (err) {
            if (err && err.name === 'AbortError') return;
            if (mine !== seq) return;
            finish();
            if (opts.body) {                       // can't safely replay a POST as a page load
                undo(opts);
                toast('Something went wrong' + (err && err.status ? ' (' + err.status + ')' : '') + '. Please try again.');
            } else {
                fallback(url);
            }
        });
    }

    function undo(opts) {
        if (opts.form) { opts.form.dataset.busy = ''; }
        if (opts.button) { opts.button.classList.remove('busy'); opts.button.disabled = false; }
        if (opts.card) opts.card.classList.remove('removing');
        if (opts.check) opts.check.classList.toggle('on');
    }

    function swap(html, finalUrl, opts) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var newMain = doc.querySelector('main');
        if (!newMain) return fallback(finalUrl);

        var target = new URL(finalUrl, location.href);
        var here = new URL(location.href);
        var refresh = opts.push !== false && sameDoc(target, here);   // same page again: update in place

        var header = document.querySelector('header');
        var newHeader = doc.querySelector('header');
        if (header && newHeader && header.innerHTML !== newHeader.innerHTML) header.innerHTML = newHeader.innerHTML;
        if (doc.title) document.title = doc.title;

        var oldMain = document.querySelector('main');
        var y = window.scrollY;
        var adopted = document.adoptNode(newMain);
        if (refresh || reduceMotion) adopted.classList.remove('page-enter');
        oldMain.replaceWith(adopted);

        if (opts.push !== false) {
            history.replaceState({ y: y }, '');                        // remember where we were
            if (!sameDoc(target, here) || target.hash !== here.hash) history.pushState({ y: 0 }, '', target.pathname + target.search + target.hash);
        }

        App.run(adopted);

        var anchor = target.hash && document.getElementById(decodeURIComponent(target.hash.slice(1)));
        if (anchor) anchor.scrollIntoView();
        else if (refresh) window.scrollTo(0, y);
        else window.scrollTo(0, opts.restoreY || 0);

        var auto = adopted.querySelector('[autofocus]');
        if (auto) auto.focus({ preventScroll: true });
        else if (!refresh) { adopted.setAttribute('tabindex', '-1'); adopted.focus({ preventScroll: true }); }
    }

    // ---- links ----
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a) return;
        if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('data-no-ajax')) return;
        var u = new URL(a.href, location.href);
        if (u.origin !== location.origin || !/^https?:$/.test(u.protocol) || u.pathname.indexOf('/static/') === 0) return;
        if (sameDoc(u, new URL(location.href)) && u.hash) return;     // in-page anchor: let the browser scroll
        e.preventDefault();
        navigate(u.href, {});
    });

    // ---- forms ----
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (e.defaultPrevented || !form.matches || form.hasAttribute('data-no-ajax')) return;
        var method = (form.getAttribute('method') || 'get').toLowerCase();
        if (method !== 'get' && method !== 'post') return;
        var u = new URL(form.getAttribute('action') || location.href, location.href);
        if (u.origin !== location.origin) return;
        if (form.dataset.busy) { e.preventDefault(); return; }       // ignore double submits

        e.preventDefault();
        var params = new URLSearchParams(new FormData(form));
        var opts = { form: form };
        if (method === 'get') {
            u.search = params.toString();
        } else {
            opts.body = params.toString();
            form.dataset.busy = '1';
            var btn = e.submitter || form.querySelector('button[type="submit"], button:not([type])');
            if (btn && !btn.closest('.menu')) { btn.classList.add('busy'); btn.disabled = true; opts.button = btn; }
        }

        // instant feedback for the common actions
        var card = form.closest('.card');
        if (/^\/delete\//.test(u.pathname) && card) {
            card.classList.add('removing'); opts.card = card; opts.delay = 240;
        } else if (/^\/toggle\//.test(u.pathname)) {
            var check = form.querySelector('.check');
            if (check) { check.classList.toggle('on'); check.classList.add('pop'); opts.check = check; }
            if (card && new URL(location.href).searchParams.get('view') !== 'all') {
                card.classList.add('removing'); opts.card = card; opts.delay = 240;
            }
        }
        navigate(u.href, opts);
    });

    // ---- back / forward ----
    window.addEventListener('popstate', function (e) {
        navigate(location.href, { push: false, restoreY: e.state && e.state.y });
    });
})();
