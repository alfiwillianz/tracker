// Styled dropdown for <select data-fancy>, optionally with a filter box (data-filter).
// The native select stays in the form as the source of truth (and as the fallback if this
// script fails), we only draw a nicer picker on top of it.
var App = window.App || { init: function (fn) { fn(document); } };   // app.js re-runs this on every page swap
App.init(function (scope) {
scope.querySelectorAll('select[data-fancy]').forEach(function (sel) {
    var filterable = sel.hasAttribute('data-filter');

    var wrap = document.createElement('div');
    wrap.className = 'fselect';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fselect-btn';
    btn.id = (sel.id || 'select') + '-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    var text = document.createElement('span');
    text.className = 'fselect-text';
    var caret = document.createElement('span');
    caret.className = 'fselect-caret';
    caret.setAttribute('aria-hidden', 'true');
    btn.appendChild(text);
    btn.appendChild(caret);

    var panel = document.createElement('div');
    panel.className = 'fselect-menu';
    panel.hidden = true;

    var search = null;
    if (filterable) {
        search = document.createElement('input');
        search.type = 'search';
        search.className = 'fselect-search';
        search.placeholder = 'Search…';
        search.autocomplete = 'off';
        search.setAttribute('aria-label', 'Filter options');
        panel.appendChild(search);
    }

    var list = document.createElement('ul');
    list.className = 'fselect-list';
    list.setAttribute('role', 'listbox');
    panel.appendChild(list);

    var empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = 'No match';
    empty.hidden = true;
    list.appendChild(empty);

    var items = Array.prototype.map.call(sel.options, function (opt, i) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.textContent = opt.textContent;
        li.setAttribute('data-text', opt.textContent.toLowerCase());
        if (opt.value === '') li.className = 'none';
        li.addEventListener('mousedown', function (e) { e.preventDefault(); });   // keep focus where it is
        li.addEventListener('click', function () { choose(i); close(); btn.focus(); });
        li.addEventListener('mousemove', function () { setActive(i); });
        list.insertBefore(li, empty);
        return li;
    });
    var active = sel.selectedIndex;

    function visible() {
        return items.map(function (li, i) { return i; }).filter(function (i) { return !items[i].hidden; });
    }
    function setActive(i) {
        active = i;
        items.forEach(function (li, n) { li.classList.toggle('active', n === i); });
        if (items[i]) items[i].scrollIntoView({ block: 'nearest' });
    }
    function applyFilter() {
        var terms = search ? search.value.toLowerCase().split(/\s+/).filter(Boolean) : [];
        items.forEach(function (li) {
            var t = li.getAttribute('data-text');
            li.hidden = !terms.every(function (term) { return t.indexOf(term) !== -1; });
        });
        var v = visible();
        empty.hidden = v.length > 0;
        if (v.length && v.indexOf(active) === -1) setActive(v[0]);   // keep the highlight on something visible
    }
    function sync() {
        var o = sel.options[sel.selectedIndex];
        text.textContent = o ? o.textContent : '';
        btn.classList.toggle('none', sel.value === '');
        items.forEach(function (li, n) { li.setAttribute('aria-selected', n === sel.selectedIndex ? 'true' : 'false'); });
    }
    function choose(i) {
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        sync();
    }
    function open() {
        if (search) search.value = '';
        applyFilter();
        panel.hidden = false;
        wrap.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
        setActive(sel.selectedIndex);
        if (search) search.focus();
    }
    function close() {
        panel.hidden = true;
        wrap.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
    }
    function move(step) {
        var v = visible();
        if (!v.length) return;
        var pos = v.indexOf(active);
        setActive(v[Math.max(0, Math.min(v.length - 1, pos === -1 ? 0 : pos + step))]);
    }

    // one key handler for the button and (when present) the filter box
    function onKey(e) {
        var isOpen = !panel.hidden;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!isOpen) return open();
            move(e.key === 'ArrowDown' ? 1 : -1);
        } else if (e.key === 'Enter' || (e.key === ' ' && e.target === btn)) {
            e.preventDefault();                              // never submit the form from here
            if (!isOpen) return open();
            if (visible().indexOf(active) !== -1) { choose(active); close(); btn.focus(); }
        } else if (e.key === 'Escape') {
            if (isOpen) { e.preventDefault(); close(); btn.focus(); }
        } else if (e.key === 'Tab') {
            if (isOpen) close();
        } else if ((e.key === 'Home' || e.key === 'End') && isOpen && e.target === btn) {
            e.preventDefault();
            var v = visible();
            if (v.length) setActive(e.key === 'Home' ? v[0] : v[v.length - 1]);
        }
    }

    btn.addEventListener('click', function () { panel.hidden ? open() : close(); });
    btn.addEventListener('keydown', onKey);
    if (search) {
        search.addEventListener('keydown', onKey);
        search.addEventListener('input', applyFilter);
    }
    document.addEventListener('click', function outside(e) {
        if (!document.contains(wrap)) { document.removeEventListener('click', outside); return; }   // page was swapped away
        if (!wrap.contains(e.target)) close();
    });

    // clicking the label should focus the visible control, not the hidden select
    var label = sel.id && document.querySelector('label[for="' + sel.id + '"]');
    if (label) label.setAttribute('for', btn.id);

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(btn);
    wrap.appendChild(panel);
    wrap.appendChild(sel);
    sync();
    sel.classList.add('fselect-native');   // hide the native one last: if anything above threw, it stays usable
});
});
