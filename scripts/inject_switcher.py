"""
inject_switcher.py

Injects a DEV/PROD environment toggle into the dbt docs index.html.
The toggle intercepts manifest.json and catalog.json requests and redirects
them to environment-specific files (manifest_dev.json, catalog_prod.json, etc.).

Usage:
    python inject_switcher.py
"""

import os

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
docs_dir = os.path.join(base_dir, "docs")
index_path = os.path.join(docs_dir, "index.html")

if not os.path.exists(index_path):
    print(f"ERROR: {index_path} not found. Run merge_docs.py first.")
    exit(1)

with open(index_path, "r") as f:
    html = f.read()

switcher_js = """
<script>
(function() {
    var env = localStorage.getItem('dbt_docs_env') || 'dev';

    function setEnv(newEnv) {
        localStorage.setItem('dbt_docs_env', newEnv);
        location.reload();
    }

    var origFetch = window.fetch;
    window.fetch = function(url, opts) {
        if (typeof url === 'string') {
            if (url.match(/manifest\\.json/)) {
                url = url.replace('manifest.json', 'manifest_' + env + '.json');
            }
            if (url.match(/catalog\\.json/)) {
                url = url.replace('catalog.json', 'catalog_' + env + '.json');
            }
        }
        return origFetch.call(this, url, opts);
    };

    var origXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        if (typeof url === 'string') {
            if (url.match(/manifest\\.json/)) {
                url = url.replace('manifest.json', 'manifest_' + env + '.json');
            }
            if (url.match(/catalog\\.json/)) {
                url = url.replace('catalog.json', 'catalog_' + env + '.json');
            }
        }
        return origXHROpen.apply(this, arguments);
    };

    document.addEventListener('DOMContentLoaded', function() {
        var switcher = document.createElement('div');
        switcher.id = 'env-switcher';
        switcher.style.cssText = 'position:fixed;top:8px;right:16px;z-index:99999;display:flex;align-items:center;gap:8px;font-family:sans-serif;font-size:13px;';
        switcher.innerHTML = '<span style="color:#fff;font-weight:600;">Environment:</span>' +
            '<select id="env-select" style="padding:4px 8px;border-radius:4px;border:1px solid #555;background:' + (env === 'prod' ? '#2e7d32' : '#1565c0') + ';color:#fff;font-weight:600;cursor:pointer;">' +
            '<option value="dev"' + (env === 'dev' ? ' selected' : '') + '>DEV</option>' +
            '<option value="prod"' + (env === 'prod' ? ' selected' : '') + '>PROD</option>' +
            '</select>';
        document.body.appendChild(switcher);
        document.getElementById('env-select').addEventListener('change', function() {
            setEnv(this.value);
        });
    });
})();
</script>
"""

html = html.replace("</head>", switcher_js + "\n</head>")

with open(index_path, "w") as f:
    f.write(html)

print("Injected environment switcher into index.html")
print("  DEV (blue) / PROD (green) toggle in top-right corner")
print("  Selection persists via localStorage")
