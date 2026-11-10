"""Query parameters shared by the staff list endpoints."""

from typing import Annotated

from fastapi import Query

# Postgres OFFSET is a bigint: anything larger made the query fail with a 500. Bound it so an
# out-of-range value is a clean 422 instead.
MAX_OFFSET = 2**63 - 1

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET)]
