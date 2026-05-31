"""Authentication helpers.

The app is still single-user — every request resolves to OWNER_USER_ID.
When real auth lands (Cloudflare Access / own session cookie / JWT),
swap the implementation of get_current_user_id() and nothing else moves.

Keeping this as a FastAPI dependency from day one means routers can
declare `user_id: int = Depends(get_current_user_id)` and stay stable
across the auth migration.
"""

from __future__ import annotations

from app.models import OWNER_USER_ID


def get_current_user_id() -> int:
    """Resolve the calling user.

    Phase 1: always returns the bootstrap owner. Phase R3 (Cloudflare
    Access) will replace this with a real session lookup.
    """
    return OWNER_USER_ID
