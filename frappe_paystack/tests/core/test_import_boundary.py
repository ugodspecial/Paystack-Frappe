"""
The dependency rules, enforced statically (no database needed):

R1  nothing outside frappe_paystack/integrations/erpnext imports ERPNext;
R2  the ERPNext adapter imports ERPNext only inside functions;
R3  the core never imports an integration or names an application module.
"""

import ast
import os
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADAPTER_DIR = os.path.join(APP_DIR, "integrations", "erpnext")
CORE_DIR = os.path.join(APP_DIR, "core")
FORBIDDEN_IN_CORE = ("erpnext", "lms", "education", "webshop", "frappe_paystack.integrations")


def python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", "tests")]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def imports(tree):
    """Yield (module, is_module_level) for every import in a tree."""
    module_level = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, id(node) in module_level
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, id(node) in module_level


def is_erpnext(module):
    return module == "erpnext" or module.startswith("erpnext.")


class TestImportBoundary(unittest.TestCase):
    def test_only_the_adapter_imports_erpnext(self):
        offenders = []
        for path in python_files(APP_DIR):
            if path.startswith(ADAPTER_DIR + os.sep):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            offenders += [f"{os.path.relpath(path, APP_DIR)}: {m}" for m, _ in imports(tree) if is_erpnext(m)]
        self.assertEqual(offenders, [], "ERPNext imported outside frappe_paystack/integrations/erpnext")

    def test_adapter_imports_erpnext_lazily(self):
        offenders = []
        for path in python_files(ADAPTER_DIR):
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            offenders += [f"{os.path.relpath(path, APP_DIR)}: {m}" for m, top in imports(tree) if is_erpnext(m) and top]
        self.assertEqual(offenders, [], "the adapter must import ERPNext inside functions")

    def test_core_is_application_agnostic(self):
        offenders = []
        for path in python_files(CORE_DIR):
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for module, _top in imports(tree):
                if any(module == name or module.startswith(name + ".") for name in FORBIDDEN_IN_CORE):
                    offenders.append(f"{os.path.relpath(path, APP_DIR)}: {module}")
        self.assertEqual(offenders, [])

    def test_hooks_point_at_import_safe_modules(self):
        """Every dotted path in hooks.py must import without ERPNext (checked by importing them)."""
        import importlib

        import frappe_paystack.hooks as hooks

        paths = set()

        def collect(value):
            if isinstance(value, str) and value.startswith("frappe_paystack.") and "." in value:
                paths.add(value)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        for name in dir(hooks):
            if not name.startswith("_"):
                collect(getattr(hooks, name))
        for path in sorted(paths):
            module, _sep, attribute = path.rpartition(".")
            try:
                target = importlib.import_module(module)
            except ModuleNotFoundError:
                target = importlib.import_module(path)
                continue
            self.assertTrue(hasattr(target, attribute), path)
