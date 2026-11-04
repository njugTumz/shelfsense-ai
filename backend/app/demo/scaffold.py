"""FIELD-owned scaffold for generated applications.

The model generates ONLY app source under ``src/``; everything here is
fixed by FIELD so dependencies, entry point, styling pipeline, and build
config stay controlled. The professional styling stack (Tailwind CSS v3
+ PostCSS + Lucide icons, all pinned) lives here — never in model
output. index.html uses a generic title since generated apps are not
limited to the restaurant scenario.
"""

from __future__ import annotations

from app.demo.restaurant_app import (
    APP_ROOT,
    TSCONFIG_JSON,
    VITE_CONFIG_TS,
    VITE_ENV_DTS,
)

SCAFFOLD_PACKAGE_JSON = """\
{
  "name": "field-generated-app",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "lucide-react": "0.441.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "~5.5.0",
    "vite": "^5.4.0",
    "tailwindcss": "3.4.13",
    "postcss": "8.4.47",
    "autoprefixer": "10.4.20"
  }
}
"""

#: ESM config (package.json has "type": "module").
TAILWIND_CONFIG_JS = """\
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx,js,jsx}"],
  theme: { extend: {} },
  plugins: [],
};
"""

POSTCSS_CONFIG_JS = """\
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
"""

SCAFFOLD_INDEX_CSS = """\
@tailwind base;
@tailwind components;
@tailwind utilities;
"""

SCAFFOLD_MAIN_TSX = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""

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
    f"{APP_ROOT}/package.json": SCAFFOLD_PACKAGE_JSON,
    f"{APP_ROOT}/vite.config.ts": VITE_CONFIG_TS,
    f"{APP_ROOT}/tsconfig.json": TSCONFIG_JSON,
    f"{APP_ROOT}/tailwind.config.js": TAILWIND_CONFIG_JS,
    f"{APP_ROOT}/postcss.config.js": POSTCSS_CONFIG_JS,
    f"{APP_ROOT}/index.html": SCAFFOLD_INDEX_HTML,
    f"{APP_ROOT}/src/vite-env.d.ts": VITE_ENV_DTS,
    f"{APP_ROOT}/src/main.tsx": SCAFFOLD_MAIN_TSX,
    f"{APP_ROOT}/src/index.css": SCAFFOLD_INDEX_CSS,
}


def scaffold_files() -> dict[str, str]:
    """Return a copy of the fixed scaffold manifest."""
    return dict(SCAFFOLD_FILES)
