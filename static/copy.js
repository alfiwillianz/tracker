// <button data-copy="#selector">: copies that element's text. Works on plain http too (falls back to execCommand).
var App = window.App || { init: function (fn) { fn(document); } };   // app.js re-runs this on every page swap
App.init(function (scope) {
    scope.querySelectorAll('[data-copy]').forEach(function (btn) {
        var label = btn.textContent;
        btn.addEventListener('click', function () {
            var el = document.querySelector(btn.getAttribute('data-copy'));
            if (!el) return;
            var text = el.textContent.trim();

            function done(ok) {
                btn.textContent = ok ? 'Copied ✓' : 'Press Ctrl+C';
                if (!ok) { var r = document.createRange(); r.selectNodeContents(el); var s = getSelection(); s.removeAllRanges(); s.addRange(r); }
                setTimeout(function () { btn.textContent = label; }, 1800);
            }
            function fallback() {
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.style.cssText = 'position:fixed;left:-9999px;top:0';
                document.body.appendChild(ta);
                ta.select();
                var ok = false;
                try { ok = document.execCommand('copy'); } catch (e) {}
                document.body.removeChild(ta);
                done(ok);
            }
            if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
            else fallback();
        });
    });
});
