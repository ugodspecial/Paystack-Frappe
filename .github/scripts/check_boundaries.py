"""
Static checks that need no bench (run by the "static" CI job):

* nothing outside frappe_paystack/integrations/erpnext imports ERPNext, and the
  adapter imports it only inside functions (the tests of the adapter follow the
  same rule: they import ERPNext lazily and skip themselves without it);
* hooks.required_apps names only Payments and pyproject declares no ERPNext dependency.
"""

import ast
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "frappe_paystack"
ADAPTER = APP / "integrations" / "erpnext"
TESTS = APP / "tests"
errors = []


def erpnext_imports(tree):
    top = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name == "erpnext" or name.startswith("erpnext."):
                yield name, id(node) in top, node.lineno


for path in APP.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    in_adapter = ADAPTER in path.parents or TESTS in path.parents
    for name, module_level, line in erpnext_imports(tree):
        if not in_adapter:
            errors.append(f"{path.relative_to(ROOT)}:{line} imports {name} outside the ERPNext adapter")
        elif module_level:
            errors.append(f"{path.relative_to(ROOT)}:{line} imports {name} at module level")

hooks = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
for node in hooks.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "required_apps" for t in node.targets):
        apps = ast.literal_eval(node.value)
        # Entries may be "payments" or "frappe/payments" (optionally "@branch").
        if [app.split("@")[0].rstrip("/").split("/")[-1] for app in apps] != ["payments"]:
            errors.append(f"hooks.required_apps is {apps}, expected only payments")

pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
deps = pyproject.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
if "erpnext" in deps:
    errors.append("pyproject.toml lists erpnext under [tool.bench.frappe-dependencies]")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("boundaries OK")
