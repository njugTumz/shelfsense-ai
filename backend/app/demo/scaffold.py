"""FIELD-owned scaffold for generated applications.

The model generates ONLY app source under ``src/``; everything here is
fixed by FIELD so dependencies, entry point, and build config stay
controlled. Package/config contents reuse the proven demo starter;
index.html uses a generic title since generated apps are not limited to
the restaurant scenario.
"""

from __future__ import annotations

from app.demo.restaurant_app import (
    APP_ROOT,
    MAIN_TSX,
    PACKAGE_JSON,
    TSCONFIG_JSON,
    VITE_CONFIG_TS,
    VITE_ENV_DTS,
)

SCAFFOLD_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>FIELD generated app</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

SCAFFOLD_FILES: dict[str, str] = {
    f"{APP_ROOT}/package.json": PACKAGE_JSON,
    f"{APP_ROOT}/vite.config.ts": VITE_CONFIG_TS,
    f"{APP_ROOT}/tsconfig.json": TSCONFIG_JSON,
    f"{APP_ROOT}/index.html": SCAFFOLD_INDEX_HTML,
    f"{APP_ROOT}/src/vite-env.d.ts": VITE_ENV_DTS,
    f"{APP_ROOT}/src/main.tsx": MAIN_TSX,
}


def scaffold_files() -> dict[str, str]:
    """Return a copy of the fixed scaffold manifest."""
    return dict(SCAFFOLD_FILES)
