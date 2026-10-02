"""
Browser-facing URLs that stay reachable on localhost, Docker and behind proxies.

`frappe.utils.get_url()` builds an absolute URL from `host_name`, from the Host
header of the live request, or - with no request at all - from the site name.
None of those is guaranteed to carry the port the payer's browser is actually
using:

* a site called `erp.localhost` served by `bench start` is reached at
  `http://erp.localhost:8000`, but a link built off the site name is
  `http://erp.localhost`;
* nginx (and the frappe_docker images) forward `proxy_set_header Host $host`,
  which drops the port, so the same thing happens behind a reverse proxy.

Either way the payer lands on `http://erp.localhost/paystack-checkout/<id>`,
port 80, where nothing is listening: "erp.localhost refused to connect"
(ERR_CONNECTION_REFUSED) and a blank page instead of the checkout.

So a browser-facing URL is built from, in order:

1. `host_name` from site_config, when an administrator pinned one *and* the
   browser is on a different host - that is the site's published address;
2. the origin the browser reported (`origin` argument of the checkout and desk
   endpoints, i.e. `window.location.origin`). It is accepted only when its host
   is one of this site's hosts, so a forged Host header cannot aim a payment
   link - or a Paystack `callback_url` - somewhere else. On the host the site
   is published at, it is what corrects a missing port;
3. the live request (`X-Forwarded-Host` / `X-Forwarded-Port` /
   `X-Forwarded-Proto`, then the Host header), port kept - and, when that
   leaves no port (the nginx `Host $host` case), completed with the port the
   browser itself named in `Origin`/`Referer`. Consumers such as ERPNext's
   Payment Request and LMS call `get_payment_url()` themselves, without an
   `origin` argument, so those two headers are the only place the payer's real
   port appears on such a request;
4. `frappe.utils.get_url()`, for background jobs, the scheduler and
   `bench execute`.

Redirects that stay on this site are handed to the browser as paths
("/payment-success?doctype=..."), the way every other Payments gateway does it,
so the payer's browser keeps the origin - and the port - it is already on.
"""

from __future__ import annotations

from typing import Optional, Set
from urllib.parse import urlsplit, urlunsplit

import frappe
from frappe.utils import get_url

# Where a validated browser origin is kept for the rest of the request.
ORIGIN_FLAG = "paystack_browser_origin"

SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": "80", "https": "443"}


def _first(value: Optional[str]) -> str:
    """The first entry of a possibly comma-joined proxy header."""
    return (value or "").split(",")[0].strip()


def _header(name: str) -> str:
    try:
        return _first(frappe.get_request_header(name))
    except Exception:
        return ""


def _has_port(host: str) -> bool:
    """True for `host:8000` and `[::1]:8000`, false for `host` and `[::1]`."""
    return ":" in host and not host.endswith("]") and host.rsplit(":", 1)[-1].isdigit()


def _hostname(value: Optional[str]) -> str:
    """The lowercase host of a URL, an origin or a Host header, without the port."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def _configured_url() -> str:
    try:
        return get_url() or ""
    except Exception:
        return ""


def _configured_origin() -> str:
    """
    The origin an administrator pinned in site_config (`host_name`), if any.

    `get_url()` is asked for it, so Frappe's own rules (scheme, and the
    development web server port) still apply. An explicit `host_name` is the
    canonical public address of the site and stays canonical: only a browser
    on the *same* host may correct it, which is what fixes a missing port.
    """
    conf = getattr(frappe.local, "conf", None) or {}
    if not (conf.get("host_name") or conf.get("hostname")):
        return ""
    return _configured_url().rstrip("/")


def site_hosts() -> Set[str]:
    """Every host name this site answers to, as far as this request can tell."""
    request = getattr(frappe.local, "request", None)
    conf = getattr(frappe.local, "conf", None) or {}
    candidates = (
        conf.get("host_name"),
        conf.get("hostname"),
        getattr(frappe.local, "site", None),
        _header("X-Forwarded-Host"),
        getattr(request, "host", None),
        _configured_url(),
    )
    return {host for host in (_hostname(value) for value in candidates) if host}


def _reported_origin() -> str:
    """
    `scheme://host[:port]` as the browser itself wrote it, from `Origin` or
    `Referer`, or "" when neither header carries a usable absolute URL.

    Both headers are set by the browser and, unlike `Host`, survive an nginx
    `proxy_set_header Host $host` with their port intact. `Origin` is sent on
    the XHR/fetch calls the portal and the desk make; `Referer` covers an
    ordinary page load (the "Pay with Paystack" button of ERPNext or LMS).
    """
    for name in ("Origin", "Referer"):
        value = _header(name)
        if not value or value.lower() == "null":
            continue
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        scheme = (parts.scheme or "").lower()
        netloc = parts.netloc
        # Credentials in a reported origin are never legitimate here.
        if scheme in SCHEMES and netloc and "@" not in netloc:
            return f"{scheme}://{netloc.lower()}"
    return ""


def request_origin() -> Optional[str]:
    """`scheme://host[:port]` of the request being served, or None outside one."""
    request = getattr(frappe.local, "request", None)
    if request is None:
        return None

    host = _header("X-Forwarded-Host") or getattr(request, "host", "") or _header("Host")
    if not host:
        return None

    scheme = (_header("X-Forwarded-Proto") or getattr(request, "scheme", "") or "http").lower()
    if scheme not in SCHEMES:
        scheme = "http"

    if not _has_port(host):
        port = _header("X-Forwarded-Port")
        if port.isdigit() and port != DEFAULT_PORTS[scheme]:
            host = f"{host}:{port}"

    host = host.lower()
    reported = _reported_origin()
    # Only the browser's own view of *this* host may complete what the proxy
    # dropped: the port it is really on, and https when X-Forwarded-Proto is
    # missing. A reported origin on another host is ignored outright, so it
    # can never aim a payment link somewhere else.
    if reported and _hostname(reported) == _hostname(host):
        reported_scheme, _, reported_host = reported.partition("://")
        if not _has_port(host) and _has_port(reported_host):
            host = reported_host
        if scheme == "http" and reported_scheme == "https":
            scheme = "https"

    return f"{scheme}://{host}"


def normalise_origin(origin: Optional[str]) -> Optional[str]:
    """Return `scheme://host[:port]` when `origin` is one of this site's origins."""
    if not origin:
        return None

    text = str(origin).strip()
    if not text or text.lower() == "null":
        return None
    if "//" not in text:
        text = "//" + text

    try:
        parts = urlsplit(text)
    except ValueError:
        return None

    scheme = (parts.scheme or _default_scheme()).lower()
    if scheme not in SCHEMES:
        return None

    netloc = parts.netloc
    # Credentials in an origin are never legitimate here.
    if not netloc or "@" in netloc:
        return None
    if (parts.hostname or "").lower() not in site_hosts():
        return None

    return f"{scheme}://{netloc.lower()}"


def _default_scheme() -> str:
    """The scheme to assume for an origin that arrived without one."""
    return (urlsplit(request_origin() or _configured_url() or "http://").scheme or "http").lower()


def remember_origin(origin: Optional[str]) -> Optional[str]:
    """Record the browser origin a whitelisted call reported, when it is ours."""
    value = normalise_origin(origin)
    if value:
        frappe.local.flags[ORIGIN_FLAG] = value
    return value


def browser_origin() -> Optional[str]:
    return (getattr(frappe.local, "flags", None) or {}).get(ORIGIN_FLAG)


def site_origin() -> str:
    """
    The origin a link handed to a browser should point at.

    A configured `host_name` wins, because that is the address the site is
    published at - unless the browser is on that very host, in which case the
    browser knows the scheme and the port better than the configuration does.
    """
    candidate = browser_origin() or request_origin()
    configured = _configured_origin()
    if configured and (not candidate or _hostname(candidate) != _hostname(configured)):
        return configured.rstrip("/")
    return (candidate or _configured_url() or "").rstrip("/")


def site_url(path: str = "") -> str:
    """An absolute URL on this site, keeping the port the browser is using."""
    if path and path.lower().startswith(("http://", "https://")):
        return path
    base = site_origin().rstrip("/")
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def is_same_site(url: Optional[str]) -> bool:
    host = _hostname(url)
    return bool(host) and host in site_hosts()


def browser_path(url: Optional[str]) -> Optional[str]:
    """
    Return a redirect the payer's browser can follow without changing origin.

    An absolute URL on this site becomes a path, so a link built with the wrong
    port (or the wrong host) cannot strand the payer; anything else - another
    site, a relative path already - is handed back untouched.
    """
    if not url:
        return url

    text = str(url).strip()
    if not text or not text.lower().startswith(("http://", "https://")):
        return text
    if not is_same_site(text):
        return text

    parts = urlsplit(text)
    return urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))
