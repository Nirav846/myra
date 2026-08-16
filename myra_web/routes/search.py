from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/symbols")
async def search_symbols(q: str = Query(..., min_length=1)):
    from myra_app.librarian import Librarian

    lib = Librarian(read_only=True)
    return lib.search_symbols(q)
