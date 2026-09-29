"""QR codes for checkout links, written as SVG data URIs (pyqrcode ships with Frappe)."""

from base64 import b64encode
from io import BytesIO

QR_SCALE = 4
QR_QUIET_ZONE = 4
SVG_DATA_URI_PREFIX = "data:image/svg+xml;base64,"


def qr_svg(text: str) -> str:
    import pyqrcode

    stream = BytesIO()
    pyqrcode.create(text).svg(
        stream, scale=QR_SCALE, quiet_zone=QR_QUIET_ZONE, background="#ffffff", module_color="#000000"
    )
    return stream.getvalue().decode().replace("\n", "")


def qr_data_uri(text: str) -> str:
    if not text:
        return ""
    return SVG_DATA_URI_PREFIX + b64encode(qr_svg(text).encode()).decode()
