// Styled confirmation pop-up for <form data-confirm="message" data-confirm-title="..." data-confirm-ok="Delete">.
// Uses a native <dialog>; falls back to the browser's confirm() where <dialog> isn't supported.
(function () {
    var dlg, titleEl, msgEl, okBtn, cancelBtn, pending = null;

    function build() {
        dlg = document.createElement('dialog');
        dlg.className = 'confirm';
        dlg.setAttribute('aria-labelledby', 'confirm-title');
        dlg.setAttribute('aria-describedby', 'confirm-msg');

        var inner = document.createElement('div');
        inner.className = 'confirm-box';
        titleEl = document.createElement('h3');
        titleEl.id = 'confirm-title';
        msgEl = document.createElement('p');
        msgEl.id = 'confirm-msg';
        var actions = document.createElement('div');
        actions.className = 'confirm-actions';
        cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.textContent = 'Cancel';
        okBtn = document.createElement('button');
        okBtn.type = 'button';
        okBtn.className = 'ok';
        actions.appendChild(cancelBtn);
        actions.appendChild(okBtn);
        inner.appendChild(titleEl);
        inner.appendChild(msgEl);
        inner.appendChild(actions);
        dlg.appendChild(inner);
        document.body.appendChild(dlg);

        cancelBtn.addEventListener('click', function () { dlg.close(); });
        okBtn.addEventListener('click', function () {
            var form = pending;
            pending = null;
            dlg.close();
            if (form) resubmit(form);
        });
        dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });   // click on the backdrop
        dlg.addEventListener('close', function () { pending = null; });
    }

    // after OK, submit again with a marker so we don't ask twice; requestSubmit() fires the submit
    // event, which lets app.js send it in the background instead of reloading the page
    function resubmit(form) {
        form.setAttribute('data-confirmed', '1');
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form.matches || !form.matches('form[data-confirm]')) return;
        if (form.hasAttribute('data-confirmed')) { form.removeAttribute('data-confirmed'); return; }
        e.preventDefault();
        var message = form.getAttribute('data-confirm');
        if (typeof HTMLDialogElement === 'undefined') {
            if (window.confirm(message)) resubmit(form);
            return;
        }
        if (!dlg) build();
        pending = form;
        titleEl.textContent = form.getAttribute('data-confirm-title') || 'Are you sure?';
        msgEl.textContent = message;
        okBtn.textContent = form.getAttribute('data-confirm-ok') || 'Confirm';
        dlg.showModal();
        cancelBtn.focus();                   // safest default for destructive actions
    }, true);
})();
