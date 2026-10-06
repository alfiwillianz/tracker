// Live member list: filtering and paging swap just the list, no page reload.
// Without JS the filter box is a normal GET form and the pager links are normal links.
var App = window.App || { init: function (fn) { fn(document); } };   // app.js re-runs this on every page swap
App.init(function (scope) {
    var form = scope.querySelector('form.member-filter');
    var box = scope.querySelector('#member-list');
    if (!form || !box) return;
    var input = form.querySelector('input[name="q"]');
    var timer = null, seq = 0, controller = null;

    function urlFor(q) {
        var u = new URL(form.getAttribute('action'), location.href);
        if (q) u.searchParams.set('q', q);
        return u;
    }

    // mode: 'push' | 'replace'. Back/forward is handled by app.js, which reloads the whole page state.
    function load(url, mode) {
        var mine = ++seq;
        if (controller) controller.abort();
        controller = new AbortController();
        box.classList.add('loading');
        fetch(url.pathname + url.search, {
            headers: { 'X-Requested-With': 'fetch' },
            credentials: 'same-origin',
            signal: controller.signal
        }).then(function (r) {
            // a redirect means the session expired or similar: let the browser handle it normally
            if (!r.ok || r.redirected) throw new Error('bad response');
            return r.text();
        }).then(function (html) {
            if (mine !== seq || !document.contains(box)) return;      // newer request, or page swapped away
            box.innerHTML = html;
            box.classList.remove('loading');
            history[mode + 'State'](history.state, '', url.pathname + url.search + url.hash);
        }).catch(function (err) {
            if (err && err.name === 'AbortError') return;
            location.href = url.href;                                  // fall back to a full page load
        });
    }

    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { load(urlFor(input.value.trim()), 'replace'); }, 150);
    });
    form.addEventListener('submit', function (e) {                     // Enter
        e.preventDefault();
        clearTimeout(timer);
        load(urlFor(input.value.trim()), 'replace');
    });
    box.addEventListener('click', function (e) {                       // pager links (delegated: the list gets replaced)
        var a = e.target.closest && e.target.closest('.pager a');
        if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        load(new URL(a.href), 'push');
    });
});
