from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
step_up_code_var: ContextVar[str | None] = ContextVar("step_up_code", default=None)
