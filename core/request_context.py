"""Per-request facts the audit trail records without every caller threading
them through: who is calling from where. Set once by the request middleware
(core/correlation.py), read by services/audit_service.py."""

from contextvars import ContextVar

client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
