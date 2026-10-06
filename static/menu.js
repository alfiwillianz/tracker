// Close ⋯ menus on outside click, Escape, or when another one opens.
(function () {
    var menus = function () { return document.querySelectorAll('details.kebab[open]'); };
    document.addEventListener('click', function (e) {
        menus().forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') menus().forEach(function (d) { d.open = false; });
    });
    document.addEventListener('toggle', function (e) {
        if (!e.target.matches || !e.target.matches('details.kebab') || !e.target.open) return;
        menus().forEach(function (d) { if (d !== e.target) d.open = false; });
    }, true);
})();
