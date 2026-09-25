"""Names, statuses and Paystack facts shared by the gateway core."""

# Service name written on every Integration Request this app files.
PAYSTACK_SERVICE = "Paystack"

# The Payment Gateway record that existing installations (and ERPNext Payment
# Gateway Accounts) already point at. Additional accounts register as
# "Paystack-<setting name>" when asked to.
DEFAULT_GATEWAY_NAME = "Paystack"
GATEWAY_NAME_PREFIX = "Paystack-"

API_BASE = "https://api.paystack.co"

# DocTypes owned by this app.
GATEWAY_SETTING = "Paystack Gateway Setting"
PAYMENT_LOG = "Paystack Payment Log"
REFUND_LOG = "Paystack Refund Log"
AUTHORIZATION = "Paystack Customer Authorization"
SETTLEMENT = "Paystack Settlement"
RECONCILIATION_LOG = "Paystack Reconciliation Log"

INTEGRATION_REQUEST = "Integration Request"

# Role that may move money (refunds, saved-card charges, manual completion)
# without being a System Manager. Adapters may add roles through the
# `paystack_manager_roles` hook.
PAYSTACK_MANAGER = "Paystack Manager"
SYSTEM_MANAGER = "System Manager"

# Checkout modes.
CHECKOUT_INLINE = "Inline"
CHECKOUT_HOSTED = "Hosted"

# ---------------------------------------------------------------------------
# Payment session (Paystack Payment Log) statuses: what Paystack did.
# Bookkeeping in a consuming application is tracked elsewhere (adapters).
# ---------------------------------------------------------------------------
PENDING = "Pending"
PAID = "Paid"
FAILED = "Failed"
CANCELLED = "Cancelled"
EXPIRED = "Expired"
NEEDS_ATTENTION = "Needs Attention"
PARTIALLY_REFUNDED = "Partially Refunded"
REFUNDED = "Refunded"

SESSION_STATUSES = (
    PENDING,
    PAID,
    FAILED,
    CANCELLED,
    EXPIRED,
    NEEDS_ATTENTION,
    PARTIALLY_REFUNDED,
    REFUNDED,
)

# Money was captured.
CAPTURED_STATUSES = (PAID, PARTIALLY_REFUNDED, REFUNDED)

# A new checkout attempt may start from these (subject to expiry).
RETRYABLE_STATUSES = (PENDING, FAILED, CANCELLED)

# Money that can still be refunded.
REFUNDABLE_STATUSES = (PAID, PARTIALLY_REFUNDED)

# Upstream (15.x) statuses, kept only so migrations and shims can read them.
LEGACY_PROCESSED = "Processed"
LEGACY_COMPLETED = "Completed"

# ---------------------------------------------------------------------------
# Consumer notification state (independent of the gateway status).
# ---------------------------------------------------------------------------
NOTIFY_PENDING = "Pending"
NOTIFY_DONE = "Notified"
NOTIFY_FAILED = "Failed"
NOTIFY_NEEDS_ATTENTION = "Needs Attention"
NOTIFY_NOT_REQUIRED = "Not Required"

NOTIFICATION_STATUSES = (
    NOTIFY_PENDING,
    NOTIFY_DONE,
    NOTIFY_FAILED,
    NOTIFY_NEEDS_ATTENTION,
    NOTIFY_NOT_REQUIRED,
)

# Attempts before a failing consumer callback is escalated to Needs Attention.
NOTIFY_MAX_ATTEMPTS = 8

# How a capture reached the session (audit trail).
VIA_CALLBACK = "Callback"
VIA_WEBHOOK = "Webhook"
VIA_SWEEP = "Sweep"
VIA_MANUAL = "Manual"
VIA_CHARGE = "Charge"

# ---------------------------------------------------------------------------
# Refunds.
# ---------------------------------------------------------------------------
REFUND_PENDING = "Pending"
REFUND_PROCESSING = "Processing"
REFUND_PROCESSED = "Processed"
REFUND_FAILED = "Failed"

REFUND_STATUSES = (REFUND_PENDING, REFUND_PROCESSING, REFUND_PROCESSED, REFUND_FAILED)

# Refunds that hold part of the refundable balance.
REFUND_OPEN_STATUSES = (REFUND_PENDING, REFUND_PROCESSING)

# Paystack refund states mapped to Refund Log statuses.
PAYSTACK_REFUND_STATUS_MAP = {
    "pending": REFUND_PENDING,
    "processing": REFUND_PROCESSING,
    "needs-attention": REFUND_PROCESSING,
    "processed": REFUND_PROCESSED,
    "failed": REFUND_FAILED,
    "reversed": REFUND_FAILED,
}

# ---------------------------------------------------------------------------
# Paystack transaction statuses.
# ---------------------------------------------------------------------------
TX_SUCCESS = "success"
TX_FAILED = "failed"
TX_ABANDONED = "abandoned"
TX_REVERSED = "reversed"
# Anything else (ongoing, pending, processing, queued, ...) is still in flight.

# ---------------------------------------------------------------------------
# Webhooks.
# ---------------------------------------------------------------------------
EVENT_CHARGE_SUCCESS = "charge.success"
REFUND_EVENTS = ("refund.pending", "refund.processing", "refund.processed", "refund.failed")
# Not in Paystack's documented event list; kept because upstream handled them.
SETTLEMENT_EVENTS = ("settlement.success", "settlement.processed")

SIGNATURE_HEADER = "x-paystack-signature"

# Paystack's published webhook source addresses (test and live). Used only as
# a suggestion in the settings form; the allowlist is opt-in.
PAYSTACK_WEBHOOK_IPS = ("52.31.139.75", "52.49.173.169", "52.214.14.220")

# Integration Request statuses used for received webhook events.
EVENT_QUEUED = "Queued"
EVENT_COMPLETED = "Completed"
EVENT_FAILED = "Failed"

# ---------------------------------------------------------------------------
# Broadcast hooks other apps may subscribe to in their hooks.py. Each hook
# receives the relevant document and must be idempotent.
# ---------------------------------------------------------------------------
HOOK_SESSION_CREATED = "paystack_session_created"
HOOK_PAYMENT_SUCCEEDED = "paystack_payment_succeeded"
HOOK_PAYMENT_FAILED = "paystack_payment_failed"
HOOK_REFUND_UPDATED = "paystack_refund_updated"
HOOK_AUTHORIZATION_CAPTURED = "paystack_authorization_captured"
HOOK_SETTLEMENT_RECORDED = "paystack_settlement_recorded"

# Registries other apps extend in their hooks.py.
HOOK_REFERENCE_ADAPTERS = "paystack_reference_adapters"
HOOK_ACCOUNT_RESOLVERS = "paystack_account_resolvers"
HOOK_MANAGER_ROLES = "paystack_manager_roles"
HOOK_CHECKOUT_CONTEXT = "paystack_checkout_context"
