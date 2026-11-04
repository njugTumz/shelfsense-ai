#!/usr/bin/env python3
"""Smoke test: minimal Vite React starter in E2B — install, build, serve.

PASS = npm install + build exit 0 AND preview URL returns 200 with the marker.
The sandbox is always killed (success or failure). Max cost is capped by the
sandbox timeout printed below.
Run: backend/.venv/bin/python scripts/smoke/e2b_react.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import cost_gate, fail_with_error, get_env, report  # noqa: E402

try:
    import httpx
    from e2b import Sandbox
except ImportError as exc:  # real import error, not a sandbox failure
    print(f"CONFIG ERROR: {exc}. Run: backend/.venv/bin/pip install -r scripts/smoke/requirements.txt")
    raise SystemExit(2)

NAME = "e2b_react"
PORT = 5173
SANDBOX_TIMEOUT_S = 1200  # 20 min kill cap -> max ~$0.056 at default size
MARKER = "FIELD E2B smoke OK"

FILES = {
    "/home/user/app/package.json": """\
{
  "name": "field-e2b-smoke",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "~5.5.0",
    "vite": "^5.4.0"
  }
}
""",
    "/home/user/app/vite.config.ts": """\
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  preview: { port: 5173, host: "0.0.0.0", allowedHosts: [".e2b.app"] },
});
""",
    "/home/user/app/tsconfig.json": """\
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
""",
    "/home/user/app/index.html": """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>E2B smoke</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
""",
    "/home/user/app/src/main.tsx": """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
""",
    "/home/user/app/src/App.tsx": f"""\
import {{ useState }} from "react";

export default function App() {{
  const [count, setCount] = useState(0);
  return (
    <main style={{{{ fontFamily: "sans-serif", padding: 24 }}}}>
      <h1>{MARKER}</h1>
      <button onClick={{() => setCount((c) => c + 1)}}>count: {{count}}</button>
    </main>
  );
}}
""",
}


def main() -> int:
    started = time.perf_counter()
    api_key = get_env("E2B_API_KEY")
    cost_gate(SANDBOX_TIMEOUT_S * 0.000046, "20-min kill cap @ default 2vCPU/4GiB ($0.000046/s)")

    sbx = None
    try:
        t0 = time.perf_counter()
        sbx = Sandbox.create(timeout=SANDBOX_TIMEOUT_S, api_key=api_key)
        print(f"sandbox_id={sbx.sandbox_id} create_s={time.perf_counter() - t0:.1f}")

        node = sbx.commands.run("node --version && npm --version", timeout=60)
        print(f"node: {node.stdout.strip() or node.stderr.strip()}")
        if node.exit_code != 0:
            return fail_with_error(NAME, started, "NO NODE IN SANDBOX",
                                   f"stdout={node.stdout}\nstderr={node.stderr}")

        t0 = time.perf_counter()
        for path, data in FILES.items():
            sbx.files.write(path, data)
        print(f"uploaded {len(FILES)} files_s={time.perf_counter() - t0:.1f}")

        def run_checked(label, cmd, **kwargs):
            """commands.run raises CommandExitException (carrying stdout/stderr) on failure."""
            t0 = time.perf_counter()
            try:
                result = sbx.commands.run(cmd, **kwargs)
                print(f"{label} exit={result.exit_code} s={time.perf_counter() - t0:.1f}")
                return result, ""
            except Exception as exc:  # noqa: BLE001
                out = getattr(exc, "stdout", "") or ""
                err = getattr(exc, "stderr", "") or ""
                code = getattr(exc, "exit_code", "?")
                print(f"{label} raised {type(exc).__name__} exit={code} s={time.perf_counter() - t0:.1f}")
                return None, f"{exc}\nstdout={out[-3000:]}\nstderr={err[-3000:]}"

        install, install_err = run_checked(
            "npm_install", "npm install --no-audit --no-fund", cwd="/home/user/app", timeout=600)
        if install is None:
            return fail_with_error(NAME, started, "NPM INSTALL FAILED", install_err)

        build, build_err = run_checked(
            "build", "npm run build", cwd="/home/user/app", timeout=600)
        if build is None:
            return fail_with_error(NAME, started, "BUILD FAILED", build_err)

        server = sbx.commands.run("npm run preview -- --port 5173 --host 0.0.0.0",
                                  cwd="/home/user/app", background=True, timeout=60)
        url = f"https://{sbx.get_host(PORT)}"
        print(f"preview_url={url}")

        import re

        body, bundle_body, status = "", "", 0
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                resp = httpx.get(url, timeout=10.0, follow_redirects=True)
                status, body = resp.status_code, resp.text
                if status == 200 and '<div id="root">' in body:
                    # Marker is client-rendered; verify it inside the served JS bundle.
                    for asset in re.findall(r'src="(/assets/[^"]+\.js)"', body):
                        bundle = httpx.get(f"{url}{asset}", timeout=10.0)
                        bundle_body += bundle.text
                    if MARKER in bundle_body:
                        break
            except httpx.HTTPError:
                pass
            time.sleep(3)
        if status != 200 or MARKER not in bundle_body:
            return fail_with_error(NAME, started, f"SERVE CHECK FAILED (status={status})",
                                   body[:2000] + f"\nbundle_bytes={len(bundle_body)}")
        elapsed = time.perf_counter() - started
        return report(NAME, True, elapsed,
                      f"preview_url={url} status=200 marker_in_bundle=true "
                      f"approx_cost=${elapsed * 0.000046:.4f} (killed after check)")
    except Exception as exc:  # noqa: BLE001 - record the real failure, then kill
        return fail_with_error(NAME, started, f"EXCEPTION: {type(exc).__name__}", str(exc))
    finally:
        if sbx is not None:
            try:
                sbx.kill()
                print("sandbox killed")
            except Exception as exc:  # noqa: BLE001
                print(f"WARNING: sandbox kill failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
