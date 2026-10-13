"""RFC 7807 problem details."""

from collections.abc import Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

BASE = "https://stozher.example/problems/"


class ApiError(HTTPException):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status, detail=detail or title, headers=headers)
        self.slug, self.title = slug, title


def _problem(
    status: int, slug: str, title: str, detail: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body = {"type": BASE + slug, "title": title, "status": status, "detail": detail}
    return JSONResponse(
        body, status_code=status, media_type="application/problem+json", headers=headers
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> JSONResponse:
        return _problem(exc.status_code, exc.slug, exc.title, str(exc.detail), exc.headers)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, "http-error", "HTTP error", str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        return _problem(
            422, "validation-error", "Invalid request", f"{loc}: {first.get('msg', '')}"
        )
