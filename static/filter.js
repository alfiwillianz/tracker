// Filter box that submits itself shortly after you stop typing (Enter works too, with or without JS).
document.querySelectorAll('input[data-autosubmit]').forEach(function (input) {
    var timer = null;
    var last = input.value;
    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
            if (input.value !== last) { last = input.value; input.form.submit(); }
        }, 350);
    });
    // the page reloads with the results: put the cursor back where you were typing
    if (input.value) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
    }
});
