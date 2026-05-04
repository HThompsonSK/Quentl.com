/**
 * Hash-based sub-tabs for financial-model.html (#pl | #cash | #bs | #saas).
 */
(function () {
    var ORDER = ['pl', 'cash', 'bs', 'saas'];

    function normalizeHash() {
        var raw = (window.location.hash || '#pl').replace(/^#/, '') || 'pl';
        if (ORDER.indexOf(raw) === -1) return 'pl';
        return raw;
    }

    function show(tab) {
        ORDER.forEach(function (id) {
            var panel = document.getElementById('fm-panel-' + id);
            if (panel) panel.hidden = id !== tab;
            document.querySelectorAll('[data-fm-tab="' + id + '"]').forEach(function (el) {
                el.classList.toggle('fm-subnav-link--active', id === tab);
            });
        });
    }

    function sync() {
        show(normalizeHash());
    }

    window.addEventListener('hashchange', sync);
    document.addEventListener('DOMContentLoaded', function () {
        if (!window.location.hash || window.location.hash === '#') {
            if (window.history && window.history.replaceState) {
                window.history.replaceState(null, '', window.location.pathname + window.location.search + '#pl');
            } else {
                window.location.hash = 'pl';
            }
        }
        sync();
    });
})();
