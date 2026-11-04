"""Reference restaurant application used by the verification demo/test path.

This is a controlled starter (plan.md section 11): a genuine, hand-written
React order-management dashboard. It is NOT claimed to be model-generated.
Every verification result reported against it comes from real E2B execution
and real Muse Spark inference, never from these file contents alone.

Layout mirrors the proven E2B smoke-test starter (React 18, Vite 5,
TypeScript), including the ``preview.allowedHosts`` setting generated apps
need inside E2B. ``src/lib/orderLogic.js`` is plain JavaScript on purpose:
the UI imports it for real data flow, and the E2B functional check executes
the same shipped file with node and asserts status transitions.
"""

from __future__ import annotations

APP_ROOT = "/home/user/app"

DEMO_REQUIREMENTS = """\
Problem: the restaurant tracks incoming orders on paper, so the cashier and
kitchen staff lose sight of which orders are received, in preparation, or
ready for pickup.

Users: cashier, kitchen staff.

Features:
1. View incoming orders as a list with customer name and items.
2. View order details (customer, items, current status).
3. Update an order status through Received -> Preparing -> Ready -> Completed.
4. See an operational overview with counts per status.
5. Ship with sample orders where real business data is unavailable.

Excluded: online payments, customer accounts, delivery tracking, persistence
across page reloads (in-memory demo state is acceptable for the demo).
"""

DEMO_ACCEPTANCE_CRITERIA = [
    "Orders are displayed with customer and items.",
    "Order details can be viewed.",
    "Status can be changed forward one step (Received -> Preparing -> Ready -> Completed).",
    "Invalid status changes are rejected with a visible message.",
    "Status changes update the visible interface and overview counts.",
    "The application builds successfully and the running preview can be opened.",
]

PACKAGE_JSON = """\
{
  "name": "field-demo-orders",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
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
"""

VITE_CONFIG_TS = """\
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  preview: { port: 5173, host: "0.0.0.0", allowedHosts: [".e2b.app"] },
});
"""

TSCONFIG_JSON = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowJs": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
"""

INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Restaurant Orders — FIELD demo</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

VITE_ENV_DTS = """\
/// <reference types="vite/client" />
"""

MAIN_TSX = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""

ORDER_LOGIC_JS = """\
// Pure order-status logic. Imported by the UI (App.tsx) and executed
// directly by the E2B functional check, so the test covers shipped code.
export const STATUSES = ["Received", "Preparing", "Ready", "Completed"];

const RANK = new Map(STATUSES.map((status, index) => [status, index]));

export function isValidStatus(status) {
  return RANK.has(status);
}

// Forward one step only: skips and backwards moves are rejected.
export function canTransition(from, to) {
  if (!RANK.has(from) || !RANK.has(to)) {
    return false;
  }
  return RANK.get(to) === RANK.get(from) + 1;
}

export function transition(order, to) {
  if (!order || typeof order !== "object") {
    throw new Error("order must be an object");
  }
  if (!canTransition(order.status, to)) {
    throw new Error(`invalid transition ${order.status} -> ${to}`);
  }
  return { ...order, status: to };
}

export function summarize(orders) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const order of orders) {
    if (RANK.has(order.status)) {
      counts[order.status] += 1;
    }
  }
  return counts;
}
"""

APP_TSX = """\
import { useMemo, useState } from "react";
import { STATUSES, summarize, transition } from "./lib/orderLogic.js";
import "./app.css";

interface Order {
  id: string;
  customer: string;
  items: string;
  status: string;
}

const SAMPLE_ORDERS: Order[] = [
  { id: "A-101", customer: "Amina", items: "2x Margherita, 1x Lentil soup", status: "Received" },
  { id: "A-102", customer: "Bereket", items: "1x Kitfo special", status: "Preparing" },
  { id: "A-103", customer: "Chaltu", items: "3x Espresso, 1x Cheesecake", status: "Ready" },
  { id: "A-104", customer: "Dawit", items: "1x Family platter", status: "Received" },
];

export default function App() {
  const [orders, setOrders] = useState<Order[]>(SAMPLE_ORDERS);
  const [notice, setNotice] = useState("");
  const counts = useMemo(() => summarize(orders), [orders]);

  function changeStatus(id: string, next: string): void {
    const order = orders.find((o) => o.id === id);
    if (!order) {
      setNotice(`Unknown order ${id}`);
      return;
    }
    try {
      const updated = transition(order, next) as Order;
      setOrders((prev) => prev.map((o) => (o.id === id ? updated : o)));
      setNotice("");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Status change rejected");
    }
  }

  return (
    <main className="page">
      <h1>Restaurant orders</h1>
      <p className="lede">Counter dashboard — move each order forward as it is prepared.</p>

      <section aria-label="Overview" className="overview">
        {STATUSES.map((status: string) => (
          <div key={status} className="stat">
            <span className="stat-count">{counts[status] ?? 0}</span>
            <span className="stat-label">{status}</span>
          </div>
        ))}
      </section>

      {notice !== "" && (
        <p role="alert" className="notice">
          {notice}
        </p>
      )}

      <ul className="orders">
        {orders.map((order) => (
          <li key={order.id} className="order">
            <div className="order-head">
              <strong>{order.id}</strong>
              <span>{order.customer}</span>
            </div>
            <p className="order-items">{order.items}</p>
            <label className="order-status">
              Status
              <select
                value={order.status}
                onChange={(event) => changeStatus(order.id, event.target.value)}
              >
                {STATUSES.map((status: string) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
          </li>
        ))}
      </ul>
    </main>
  );
}
"""

APP_CSS = """\
.page {
  font-family: ui-sans-serif, system-ui, sans-serif;
  max-width: 42rem;
  margin: 0 auto;
  padding: 1.5rem 1rem 3rem;
  color: #1c1917;
}
.lede {
  color: #57534e;
}
.overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.5rem;
  margin: 1rem 0;
}
.stat {
  border: 1px solid #e7e5e4;
  padding: 0.75rem 0.5rem;
  text-align: center;
}
.stat-count {
  display: block;
  font-size: 1.5rem;
  font-weight: 700;
}
.stat-label {
  font-size: 0.8rem;
  color: #57534e;
}
.notice {
  border: 1px solid #b91c1c;
  color: #991b1b;
  padding: 0.5rem 0.75rem;
}
.orders {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.75rem;
}
.order {
  border: 1px solid #e7e5e4;
  padding: 0.75rem;
}
.order-head {
  display: flex;
  gap: 0.75rem;
  align-items: baseline;
}
.order-items {
  margin: 0.25rem 0 0.5rem;
  color: #44403c;
}
.order-status {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.9rem;
}
select {
  padding: 0.35rem 0.5rem;
}
"""

DEMO_FILES: dict[str, str] = {
    f"{APP_ROOT}/package.json": PACKAGE_JSON,
    f"{APP_ROOT}/vite.config.ts": VITE_CONFIG_TS,
    f"{APP_ROOT}/tsconfig.json": TSCONFIG_JSON,
    f"{APP_ROOT}/index.html": INDEX_HTML,
    f"{APP_ROOT}/src/vite-env.d.ts": VITE_ENV_DTS,
    f"{APP_ROOT}/src/main.tsx": MAIN_TSX,
    f"{APP_ROOT}/src/App.tsx": APP_TSX,
    f"{APP_ROOT}/src/app.css": APP_CSS,
    f"{APP_ROOT}/src/lib/orderLogic.js": ORDER_LOGIC_JS,
}

#: Node assertions executed in the sandbox against the shipped orderLogic.js.
#: The test file is FIELD-owned; the logic under test is the app's own code.
ORDER_LOGIC_TEST_MJS = """\
import assert from "node:assert/strict";
import {
  STATUSES,
  canTransition,
  summarize,
  transition,
} from "/home/user/app/src/lib/orderLogic.js";

assert.deepEqual(STATUSES, ["Received", "Preparing", "Ready", "Completed"]);
assert.equal(canTransition("Received", "Preparing"), true);
assert.equal(canTransition("Preparing", "Ready"), true);
assert.equal(canTransition("Ready", "Completed"), true);
assert.equal(canTransition("Received", "Ready"), false);
assert.equal(canTransition("Ready", "Preparing"), false);
assert.equal(canTransition("Completed", "Received"), false);
assert.equal(canTransition("Bogus", "Preparing"), false);
assert.equal(transition({ id: "t", status: "Received" }, "Preparing").status, "Preparing");
assert.throws(() => transition({ id: "t", status: "Received" }, "Ready"), /invalid transition/);
assert.throws(() => transition(null, "Preparing"), /order must be an object/);
assert.deepEqual(summarize([{ status: "Received" }, { status: "Ready" }]), {
  Received: 1, Preparing: 0, Ready: 1, Completed: 0,
});
console.log("ORDER LOGIC OK");
"""

#: Genuine content strings the served bundle must contain. These are real
#: workflow string literals (statuses, sample customers, heading, logic
#: error text), not planted markers, and the check reports exactly which
#: were found. Only string literals are used: the production minifier
#: renames identifiers such as ``canTransition``.
BUNDLE_EVIDENCE_STRINGS = (
    "Received",
    "Preparing",
    "Ready",
    "Completed",
    "Amina",
    "Bereket",
    "Restaurant orders",
    "invalid transition",
    "order must be an object",
)


def demo_files() -> dict[str, str]:
    """Return a copy of the reference app file manifest."""
    return dict(DEMO_FILES)
