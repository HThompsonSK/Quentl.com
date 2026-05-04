/**
 * Injects main nav. Set <body data-nav="dashboard|financial|sales|analytics|metrics|products|costs|cashflow|funding|settings">.
 */
(function () {
    var nav = document.getElementById('site-nav');
    if (!nav) return;

    var key = (document.body && document.body.getAttribute('data-nav')) || 'dashboard';
    var items = [
        ['dashboard', 'index.html', 'Dashboard'],
        ['financial', 'financial-model.html', 'Financial model'],
        ['sales', 'sales-planner.html', 'Sales'],
        ['analytics', 'analytics.html', 'Analytics'],
        ['metrics', 'key-metrics.html', 'Metrics'],
        ['products', 'products.html', 'Products'],
        ['costs', 'operating-costs.html', 'Costs'],
        ['cashflow', 'projects.html', 'Cashflow'],
        ['funding', 'funding.html', 'Funding'],
        ['settings', 'settings.html', 'Settings']
    ];

    nav.innerHTML = items.map(function (row) {
        var k = row[0];
        var href = row[1];
        var label = row[2];
        var active = k === key ? ' site-nav-link--active' : '';
        return '<a href="' + href + '" class="site-nav-link' + active + '">' + label + '</a>';
    }).join('');
})();
