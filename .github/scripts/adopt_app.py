"""
Adopt the renamed app on a site that still names frappe_paystack, using the
app's own helper (paystack_frappe.rename_app.adopt_installed_site).

`bench execute paystack_frappe.rename_app.adopt_installed_site` cannot reach
it: frappe.get_attr only resolves methods of apps the site already lists, and
the site still lists the former name. The same applies to a user's console:
`bench --site your.site console`, then
`from paystack_frappe.rename_app import adopt_installed_site`.
Usage (from sites/): ../env/bin/python adopt_app.py <site>
"""

import sys

import frappe

site = sys.argv[1]
frappe.init(site=site, sites_path=".")
frappe.connect()

from paystack_frappe.rename_app import adopt_installed_site

result = adopt_installed_site()
frappe.db.commit()
print("adopted:", result)
