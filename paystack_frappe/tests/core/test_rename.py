"""
The 17.0 rename: the former app leaves no entry point, and its rows move in place.

The pure-rename path (a site whose Module Def is still "Frappe Paystack" and
whose patch log names frappe_paystack.patches.*) is proven end to end by CI's
upgrade job; these tests prove the merge path, the repointing functions and
the absence of every frappe_paystack.* path on a site that runs this app.
"""

import importlib
import json
import os

import frappe

from paystack_frappe.migration import (
    APP_NAME,
    FORMER_APP_NAME,
    FORMER_MODULE_NAME,
    MODULE_DEF,
    MODULE_NAME,
    RENAMED_JOBS,
    repoint_patch_logs,
    repoint_scheduled_job_methods,
    rename_app_records,
    rename_module_def,
)
from paystack_frappe.rename_app import APP, FORMER_APP, adopt_apps_txt, adopt_installed_site, read_apps_txt
from paystack_frappe.tests.support.base import PaystackTestCase

NUMBER_CARD = "Number Card"
PATCH_LOG = "Patch Log"
SCHEDULED_JOB_TYPE = "Scheduled Job Type"


def insert_patch_log(patch: str) -> str:
    doc = frappe.get_doc({"doctype": PATCH_LOG, "patch": patch})
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def insert_scheduled_job(method: str) -> str:
    """Insert a job row by SQL: its method's real row owns the derived name."""
    name = f"test-rename-{frappe.generate_hash(length=8)}"
    frappe.db.sql(
        "insert into `tabScheduled Job Type` (name, creation, modified, owner, modified_by, docstatus,"
        " method, frequency, create_log) values (%s, now(), now(), 'Administrator', 'Administrator', 0, %s, 'Cron', 1)",
        (name, method),
    )
    frappe.db.commit()
    return name


def _flatten(value):
    """Yield every string in a hook value, whatever the shape (str, list, dict)."""
    if isinstance(value, dict):
        for entries in value.values():
            yield from _flatten(entries)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


class TestRename(PaystackTestCase):
    def test_former_package_is_not_importable(self):
        self.assertRaises(ImportError, importlib.import_module, FORMER_APP)

    def test_no_hook_or_row_names_the_former_app(self):
        import paystack_frappe.hooks as hooks

        for name, value in vars(hooks).items():
            if isinstance(value, str):
                self.assertFalse(value.startswith(f"{FORMER_APP}."), f"hooks.{name} names {value}")

        for hook in ("scheduler_events", "doc_events", "after_migrate", "before_tests", "jinja"):
            for item in _flatten(frappe.get_hooks(hook)):
                self.assertFalse(str(item).startswith(f"{FORMER_APP}."), f"hook {hook} names {item}")

        self.assertEqual(frappe.get_all(PATCH_LOG, filters={"patch": ["like", f"{FORMER_APP}.%"]}, pluck="patch"), [])
        self.assertEqual(
            frappe.get_all(SCHEDULED_JOB_TYPE, filters={"method": ["like", f"{FORMER_APP}.%"]}, pluck="method"), []
        )
        self.assertEqual(
            frappe.get_all(MODULE_DEF, filters={"app_name": FORMER_APP_NAME}, pluck="name"),
            [],
        )

    def test_renamed_job_targets_resolve(self):
        for old, new in RENAMED_JOBS.items():
            self.assertNotEqual(old, new)
            self.assertTrue(callable(frappe.get_attr(new)), new)

    def test_module_def_merge_and_claim(self):
        self.addCleanup(self._drop_module_fixture)

        frappe.get_doc(
            {"doctype": MODULE_DEF, "module_name": FORMER_MODULE_NAME, "app_name": FORMER_APP_NAME}
        ).insert(ignore_permissions=True)
        card = frappe.get_doc(
            {
                "doctype": NUMBER_CARD,
                "label": "Rename Fixture Card",
                "type": "Document Type",
                "document_type": "Paystack Payment Log",
                "function": "Count",
                "is_public": 1,
                "show_percentage_stats": 0,
                "filters_json": "[]",
                "module": FORMER_MODULE_NAME,
            }
        )
        card.flags.ignore_permissions = True
        card.insert()

        # "Paystack Frappe" already exists on this site: the former module merges into it.
        self.assertEqual(rename_module_def(), MODULE_NAME)
        self.assertFalse(frappe.db.exists(MODULE_DEF, FORMER_MODULE_NAME))
        module = frappe.db.get_value(MODULE_DEF, MODULE_NAME, ["module_name", "app_name", "custom"], as_dict=True)
        self.assertEqual((module.module_name, module.app_name, module.custom), (MODULE_NAME, APP_NAME, 0))
        # the linked row followed the module
        self.assertEqual(frappe.db.get_value(NUMBER_CARD, "Rename Fixture Card", "module"), MODULE_NAME)

        # a second pass finds nothing to move and only re-claims the module
        self.assertIsNone(rename_module_def())
        self.assertEqual(frappe.db.get_value(MODULE_DEF, MODULE_NAME, "app_name"), APP_NAME)
        self.assertEqual(frappe.db.get_value(MODULE_DEF, MODULE_NAME, "custom"), 0)

    def _drop_module_fixture(self):
        frappe.db.delete(NUMBER_CARD, {"label": "Rename Fixture Card"})
        if frappe.db.exists(MODULE_DEF, FORMER_MODULE_NAME):
            frappe.delete_doc(MODULE_DEF, FORMER_MODULE_NAME, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_patch_logs_are_repointed(self):
        old = f"{FORMER_APP_NAME}.patches.v0_9_9.rename_fixture"
        name = insert_patch_log(old)
        self.addCleanup(lambda: frappe.db.delete(PATCH_LOG, name))

        renamed = repoint_patch_logs()
        new = f"{APP_NAME}.patches.v0_9_9.rename_fixture"
        self.assertIn(new, renamed)
        self.assertEqual(frappe.db.get_value(PATCH_LOG, name, "patch"), new)
        # a second pass finds nothing
        self.assertEqual(repoint_patch_logs(), [])

    def test_scheduled_jobs_are_repointed(self):
        # a job that only changed its app name is prefix-swapped...
        plain = insert_scheduled_job(f"{FORMER_APP_NAME}.utils.sweep.rename_fixture")
        self.addCleanup(lambda: frappe.db.delete(SCHEDULED_JOB_TYPE, plain))
        # ...and one whose home also moved lands on the method hooks.py names.
        moved = insert_scheduled_job(f"{FORMER_APP_NAME}.helpers.settlement.retry_unposted_settlements")
        self.addCleanup(lambda: frappe.db.delete(SCHEDULED_JOB_TYPE, moved))
        frappe.db.commit()

        renamed = repoint_scheduled_job_methods()
        self.assertIn(f"{APP_NAME}.utils.sweep.rename_fixture", renamed)
        self.assertEqual(
            frappe.db.get_value(SCHEDULED_JOB_TYPE, plain, "method"),
            f"{APP_NAME}.utils.sweep.rename_fixture",
        )
        self.assertEqual(
            frappe.db.get_value(SCHEDULED_JOB_TYPE, moved, "method"),
            f"{APP_NAME}.integrations.erpnext.settlement.retry_unposted_settlements",
        )
        # a second pass finds nothing
        self.assertEqual(repoint_scheduled_job_methods(), [])

    def test_rename_app_records_is_idempotent(self):
        first = rename_app_records()
        self.assertIsNone(first["module"])
        self.assertEqual(first["patches"], [])
        self.assertEqual(first["scheduled_jobs"], [])
        self.assertEqual(rename_app_records(), {"module": None, "patches": [], "scheduled_jobs": []})

    def test_patch_module_is_wired(self):
        from paystack_frappe.patches.v17_0 import rename_app as patch

        patch.execute()  # no former rows on this site: moves nothing, raises nothing

    def test_adopt_installed_site(self):
        original_record = frappe.db.get_value(
            "DefaultValue", {"defkey": "installed_apps", "parent": "__global"}, "defvalue"
        )
        original_apps_txt = read_apps_txt()
        self.addCleanup(self._restore_site_record, original_record, original_apps_txt)

        # a site that still names the former app
        installed = [app for app in json.loads(original_record or "[]") if app not in (APP, FORMER_APP)]
        frappe.db.set_value(
            "DefaultValue",
            {"defkey": "installed_apps"},
            "defvalue",
            json.dumps(["frappe"] + installed + [FORMER_APP]),
        )
        frappe.db.commit()

        result = adopt_installed_site()
        self.assertNotIn(FORMER_APP, result["installed_apps"])
        self.assertIn(APP, result["installed_apps"])
        self.assertIn("frappe", result["installed_apps"])
        self.assertNotIn(FORMER_APP, result["apps_txt"])
        self.assertIn(APP, result["apps_txt"])

        # adopting again changes nothing
        again = adopt_installed_site()
        self.assertEqual(again["installed_apps"], result["installed_apps"])

        # a bench whose apps.txt names the former app gets it replaced in place
        position = result["apps_txt"].index(APP) if APP in result["apps_txt"] else len(result["apps_txt"])
        self.assertGreater(position, 0)

    def test_adopt_apps_txt_replaces_in_place(self):
        original = read_apps_txt()
        self.addCleanup(self._write_apps_txt, original)

        apps_txt = os.path.join(frappe.local.sites_path, "apps.txt")
        with open(apps_txt, "w", encoding="utf-8") as handle:
            handle.write("\n".join(["frappe", "payments", FORMER_APP]) + "\n")

        apps = adopt_apps_txt(FORMER_APP)
        self.assertEqual(apps, ["frappe", "payments", APP])

    def _restore_site_record(self, record, apps_txt):
        if record is not None:
            frappe.db.set_value("DefaultValue", {"defkey": "installed_apps"}, "defvalue", record)
            frappe.db.commit()
        self._write_apps_txt(apps_txt)
        frappe.clear_cache()

    @staticmethod
    def _write_apps_txt(apps):
        apps_txt = os.path.join(frappe.local.sites_path, "apps.txt")
        with open(apps_txt, "w", encoding="utf-8") as handle:
            handle.write("\n".join(apps) + "\n")
