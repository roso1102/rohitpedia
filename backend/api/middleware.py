from uuid import UUID

from fastapi import Request


def _is_valid_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False


async def attach_tenant_context(request: Request, call_next):
    tenant_id = request.headers.get("x-user-id")
    request.state.current_tenant = tenant_id if _is_valid_uuid(tenant_id) else None
    request.state.request_source = "header" if request.state.current_tenant else "anonymous"
    return await call_next(request)
