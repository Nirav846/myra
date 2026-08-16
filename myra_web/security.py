"""
MYRA API auth. Single source of truth for the API secret and the
FastAPI dependency that guards mutable endpoints.
"""

import os

from fastapi import Header, HTTPException

MYRA_API_SECRET = os.environ.get("MYRA_API_SECRET", "myra-local-dev-2026")


async def verify_myra_auth(x_myra_auth: str = Header(None)):
    if x_myra_auth != MYRA_API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
