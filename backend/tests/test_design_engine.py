"""Keyless tests: professional generation engine scaffold and contracts.

Run from backend/: python -m pytest tests/test_design_engine.py
Proves the FIELD-owned Tailwind scaffold is complete and pinned, the demo
starter is untouched by the scaffold upgrade, and both developer prompts
carry the same scaffold spec, design contract, and architecture rules.
"""

import json

from app.demo import restaurant_app, scaffold
from app.services.auditors import AUDIT_SYSTEM
from app.services.developer import (
    ACCESSIBLE_MARKUP_CONTRACT,
    ARCHITECTURE_RULES,
    DESIGN_CONTRACT,
    DEVELOPER_SYSTEM,
    REPAIR_SYSTEM,
    SCAFFOLD_SPEC,
)


def test_scaffold_has_pinned_styling_stack() -> None:
    files = scaffold.scaffold_files()
    pkg = json.loads(files["/home/user/app/package.json"])
    assert pkg["devDependencies"]["tailwindcss"] == "3.4.13"
    assert pkg["devDependencies"]["postcss"] == "8.4.47"
    assert pkg["devDependencies"]["autoprefixer"] == "10.4.20"
    assert pkg["dependencies"]["lucide-react"] == "0.441.0"
    assert pkg["dependencies"]["react"].startswith("^18")
    assert files["/home/user/app/tailwind.config.js"].count("export default") == 1
    assert "content" in files["/home/user/app/tailwind.config.js"]
    assert "tailwindcss" in files["/home/user/app/postcss.config.js"]
    assert "@tailwind base" in files["/home/user/app/src/index.css"]
    assert 'import "./index.css"' in files["/home/user/app/src/main.tsx"]
    assert len(files) == 9


def test_scaffold_upgrade_does_not_touch_demo_starter() -> None:
    assert "tailwindcss" not in restaurant_app.PACKAGE_JSON
    assert "lucide-react" not in restaurant_app.PACKAGE_JSON
    assert "index.css" not in restaurant_app.MAIN_TSX


def test_prompts_share_one_scaffold_and_design_contract() -> None:
    for prompt in (DEVELOPER_SYSTEM, REPAIR_SYSTEM):
        assert SCAFFOLD_SPEC in prompt
        assert DESIGN_CONTRACT in prompt
        assert ACCESSIBLE_MARKUP_CONTRACT in prompt
        assert ARCHITECTURE_RULES in prompt
        assert "lucide-react" in prompt
        assert "390px" in prompt
        assert "NEEDS_BACKEND: " in prompt
    assert "src/index.css" in REPAIR_SYSTEM  # scaffold-owned, never returned


def test_auditor_stays_subordinate_on_design() -> None:
    assert "authoritative" in AUDIT_SYSTEM
    assert "never critical or high on aesthetics alone" in AUDIT_SYSTEM
    assert "restaurant" not in AUDIT_SYSTEM.lower()
