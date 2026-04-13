import json
from uuid import UUID

from fastapi import Request

SESSION_USER_COOKIE = "rp_user_id"


def _is_valid_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False


async def attach_tenant_context(request: Request, call_next):
    session_tenant = request.cookies.get(SESSION_USER_COOKIE)
    request.state.current_tenant = session_tenant if _is_valid_uuid(session_tenant) else None
    request.state.telegram_user_id = None
    request.state.request_source = "session" if request.state.current_tenant else "anonymous"

    # For Telegram webhook requests, capture telegram_id so DB dependency can
    # resolve tenant and set RLS context before writes.
    if request.url.path == "/webhook/telegram" and request.state.current_tenant is None:
        body = await request.body()
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
                telegram_id = (payload.get("message") or {}).get("from", {}).get("id")
                if telegram_id:
                    request.state.telegram_user_id = int(telegram_id)
                    request.state.request_source = "telegram"
            except (ValueError, TypeError):
                pass

    return await call_next(request)
