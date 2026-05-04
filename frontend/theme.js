(function () {
    var STORAGE_KEY = 'theme';

    function applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
        }
        window.dispatchEvent(new CustomEvent('themechange', { detail: { theme: theme } }));
    }

    function getStoredTheme() {
        var v = localStorage.getItem(STORAGE_KEY);
        if (v === 'light' || v === 'dark') return v;
        return null;
    }

    function resolveInitialTheme() {
        var stored = getStoredTheme();
        if (stored) return stored;
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function syncToggleButtons() {
        var isLight = document.documentElement.getAttribute('data-theme') === 'light';
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', isLight ? 'true' : 'false');
            btn.setAttribute('aria-label', isLight ? 'Switch to dark mode' : 'Switch to light mode');
            btn.title = isLight ? 'Switch to dark mode' : 'Switch to light mode';
        });
    }

    function toggleTheme() {
        var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
        localStorage.setItem(STORAGE_KEY, next);
        applyTheme(next);
        syncToggleButtons();
    }

    applyTheme(resolveInitialTheme());

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.addEventListener('click', toggleTheme);
        });
        syncToggleButtons();
    });
})();
