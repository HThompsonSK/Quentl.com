/**
 * Floating "Ask Quentl" panel — POST /api/ask
 * Set window.QUENTL_COMPANY_ID before load (default 1).
 */
(function () {
    var COMPANY_ID = window.QUENTL_COMPANY_ID || 1;

    var root = document.createElement('div');
    root.id = 'ask-quentl-root';
    root.className = 'ask-quentl-root';
    root.innerHTML =
        '<button type="button" class="ask-quentl-fab" id="askQuentlFab" aria-expanded="false" aria-controls="askQuentlPanel" title="Ask Quentl">' +
        '  <span aria-hidden="true">?</span>' +
        '  <span class="ask-quentl-fab-label">Ask</span>' +
        '</button>' +
        '<div class="ask-quentl-panel hidden" id="askQuentlPanel" role="dialog" aria-label="Ask Quentl">' +
        '  <div class="ask-quentl-panel__head">' +
        '    <div>' +
        '      <p class="ask-quentl-panel__title">Ask Quentl</p>' +
        '      <p class="ask-quentl-panel__sub">Plain answers from your numbers</p>' +
        '    </div>' +
        '    <button type="button" class="ask-quentl-close" id="askQuentlClose" aria-label="Close">&times;</button>' +
        '  </div>' +
        '  <div class="ask-quentl-messages" id="askQuentlMessages">' +
        '    <p class="ask-quentl-hint">Try: &ldquo;How much runway do we have?&rdquo; or &ldquo;What&rsquo;s our MRR?&rdquo;</p>' +
        '  </div>' +
        '  <form class="ask-quentl-form" id="askQuentlForm">' +
        '    <input type="text" id="askQuentlInput" class="ask-quentl-input" placeholder="Ask about runway, revenue, metrics…" autocomplete="off" maxlength="2000" />' +
        '    <button type="submit" class="btn-accent ask-quentl-send" id="askQuentlSend">Ask</button>' +
        '  </form>' +
        '</div>';

    document.body.appendChild(root);

    var fab = document.getElementById('askQuentlFab');
    var panel = document.getElementById('askQuentlPanel');
    var closeBtn = document.getElementById('askQuentlClose');
    var form = document.getElementById('askQuentlForm');
    var input = document.getElementById('askQuentlInput');
    var sendBtn = document.getElementById('askQuentlSend');
    var messages = document.getElementById('askQuentlMessages');
    var busy = false;

    function setOpen(open) {
        panel.classList.toggle('hidden', !open);
        fab.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            input.focus();
        }
    }

    function appendBubble(role, text, meta) {
        var hint = messages.querySelector('.ask-quentl-hint');
        if (hint) hint.remove();

        var wrap = document.createElement('div');
        wrap.className = 'ask-quentl-bubble ask-quentl-bubble--' + role;
        var p = document.createElement('p');
        p.textContent = text;
        wrap.appendChild(p);
        if (meta) {
            var m = document.createElement('p');
            m.className = 'ask-quentl-meta';
            m.textContent = meta;
            wrap.appendChild(m);
        }
        messages.appendChild(wrap);
        messages.scrollTop = messages.scrollHeight;
    }

    fab.addEventListener('click', function () {
        setOpen(panel.classList.contains('hidden'));
    });
    closeBtn.addEventListener('click', function () {
        setOpen(false);
    });

    form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var q = (input.value || '').trim();
        if (!q || busy) return;

        busy = true;
        sendBtn.disabled = true;
        appendBubble('user', q);
        input.value = '';

        var loading = document.createElement('div');
        loading.className = 'ask-quentl-bubble ask-quentl-bubble--assistant ask-quentl-bubble--loading';
        loading.textContent = 'Looking at your data…';
        messages.appendChild(loading);
        messages.scrollTop = messages.scrollHeight;

        fetch('/api/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company_id: COMPANY_ID, question: q })
        })
            .then(function (res) {
                return res.json().then(function (data) {
                    if (!res.ok) {
                        var detail = data && data.detail;
                        if (typeof detail === 'string') throw new Error(detail);
                        throw new Error('Request failed (' + res.status + ')');
                    }
                    return data;
                });
            })
            .then(function (data) {
                loading.remove();
                var meta = data.sources && data.sources.length
                    ? 'Based on: ' + data.sources.join(', ')
                    : '';
                appendBubble('assistant', data.answer, meta);
            })
            .catch(function (err) {
                loading.remove();
                appendBubble('assistant', err.message || 'Something went wrong. Try again in a moment.');
            })
            .finally(function () {
                busy = false;
                sendBtn.disabled = false;
                input.focus();
            });
    });
})();
