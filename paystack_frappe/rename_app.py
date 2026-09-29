"""
Adopt a site that ran this app under its former name, frappe_paystack.

The 17.0 rename moves the Python package, so a site cannot simply migrate:
the site's installed-app record still names frappe_paystack, whose patches.txt
no longer exists. This helper swaps that record (and the bench's apps.txt) so
the next `bench migrate` runs this app's patches -
paystack_frappe.patches.v17_0.rename_app renames the module and repoints the
patch and scheduler history. No document, setting or password is touched.

Call it from a console (`bench execute` cannot reach it: frappe.get_attr only
resolves methods of apps the site already lists, and the site still lists the
former name):

    bench get-app paystack_frappe https://github.com/ugodspecial/Paystack-Frappe
    bench --site your.site console
    >>> from paystack_frappe.rename_app import adopt_installed_site
    >>> adopt_installed_site()
    >>> exit()
    bench --site your.site migrate
"""

import json
import os

import frappe

FORMER_APP = "frappe_paystack"
APP = "paystack_frappe"


def adopt_installed_site(previous_app: str = FORMER_APP) -> dict:
    """
    Move the site's installed-app record from the former name to this app.

    Reads and writes the DefaultValue row directly, so a stale defaults cache
    cannot make this a read of what it is about to change. `previous_app` may
    be empty on a site that only wants its bench app list repaired. Returns
    the installed apps and the bench's app list as they stand after.
    """
    installed = read_installed_apps()
    if previous_app:
        installed = [app for app in installed if app != previous_app]
    if APP not in installed:
        installed = installed + [APP]

    frappe.db.set_value("DefaultValue", {"defkey": "installed_apps"}, "defvalue", json.dumps(installed))
    frappe.db.commit()

    adopt_apps_txt(previous_app)

    # Hook and metadata caches still describe the previous app.
    frappe.clear_cache()
    frappe.local.doc_events_hooks = None

    return {"installed_apps": installed, "apps_txt": read_apps_txt()}


def read_installed_apps() -> list:
    """Return the site's installed apps as recorded in DefaultValue (no cache)."""
    raw = frappe.db.get_value("DefaultValue", {"defkey": "installed_apps", "parent": "__global"}, "defvalue")
    if raw is None:
        raw = frappe.db.get_value("DefaultValue", {"defkey": "installed_apps"}, "defvalue")
    return json.loads(raw or "[]")


def apps_txt_path() -> str:
    """Return the bench's app list (sites/apps.txt)."""
    return os.path.join(frappe.local.sites_path, "apps.txt")


def read_apps_txt() -> list:
    if not os.path.exists(apps_txt_path()):
        return []
    with open(apps_txt_path(), encoding="utf-8") as handle:
        return [line.strip() for line in handle.read().splitlines() if line.strip()]


def adopt_apps_txt(previous_app: str) -> list:
    """
    Keep the bench's app list true once the renamed app is on the bench.

    Replaces the former app's entry (its hooks no longer load) and makes sure
    this app is listed, so frappe.init can map the new module. Returns the
    list written.
    """
    apps: list = []
    for app in read_apps_txt():
        if app == previous_app:
            app = APP
        if app not in apps:
            apps.append(app)
    if APP not in apps:
        apps.append(APP)

    with open(apps_txt_path(), "w", encoding="utf-8") as handle:
        handle.write("\n".join(apps) + "\n")

    return apps
