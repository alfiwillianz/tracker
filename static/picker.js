// Filterable multi-select for [data-picker] blocks (checkboxes stay the source of truth).
var App = window.App || { init: function (fn) { fn(document); } };   // app.js re-runs this on every page swap
App.init(function (scope) {
scope.querySelectorAll('[data-picker]').forEach(function (root) {
    var search = root.querySelector('[data-search]');
    var items = Array.prototype.slice.call(root.querySelectorAll('.picker-item'));
    var chips = root.querySelector('[data-chips]');
    var count = root.querySelector('[data-count]');
    var details = root.closest('details');
    var badge = details && details.querySelector('[data-badge]');

    function visible() { return items.filter(function (el) { return !el.hidden; }); }
    function box(el) { return el.querySelector('input'); }

    function filter() {
        var terms = search.value.toLowerCase().split(/\s+/).filter(Boolean);
        items.forEach(function (el) {
            var text = el.getAttribute('data-text');
            el.hidden = !terms.every(function (t) { return text.indexOf(t) !== -1; });
        });
    }

    function render() {
        chips.textContent = '';
        var n = 0;
        items.forEach(function (el) {
            var cb = box(el);
            el.classList.toggle('on', cb.checked);
            if (!cb.checked) return;
            n++;
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'pchip';
            chip.title = 'Remove';
            chip.textContent = cb.getAttribute('data-label') + ' ×';
            chip.addEventListener('click', function () { cb.checked = false; render(); });
            chips.appendChild(chip);
        });
        count.textContent = n ? n + ' selected' : '';
        if (badge) badge.textContent = n ? n + ' selected' : '';
    }

    search.addEventListener('input', filter);
    search.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        e.preventDefault();                       // don't submit the form from the search box
        var v = visible();
        if (v.length === 1) { box(v[0]).checked = !box(v[0]).checked; render(); }
    });
    if (details) details.addEventListener('toggle', function () { if (details.open) search.focus(); });
    root.addEventListener('change', render);
    root.querySelector('[data-all]').addEventListener('click', function () {
        visible().forEach(function (el) { box(el).checked = true; });
        render();
    });
    root.querySelector('[data-none]').addEventListener('click', function () {
        items.forEach(function (el) { box(el).checked = false; });
        render();
    });
    render();
});
});
