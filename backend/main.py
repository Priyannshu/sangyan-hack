"""Borrowed Badge -- FastAPI application.

Endpoints:
    POST /verify     run a check and return a full, explainable verdict
    GET  /health     liveness plus what external sources are used
    GET  /           the single-page frontend

Privacy note: nothing from a /verify request is written to disk or to a log.
The request body is held in memory for the duration of the call and discarded.
Uvicorn's access log records the path, not the payload.

Data note: this service holds no SEBI data.  Entity details come from what the
user reads off SEBI's own listing, and domain facts come from live RDAP.  SEBI's
portal blocks automated lookups, so the tool hands the user off to it rather
than scraping.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import HealthResponse, VerifyRequest, VerdictResponse
from .sebi_registry import registry_status
from .verdict_engine import VerificationInput, get_engine

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title=settings.api_title,
    version="2.0.0",
    description=(
        "Pre-payment verifier for Indian retail investors. Answers whether a "
        "channel (website, UPI/bank, phone) is actually attached to the entity "
        "that holds a displayed SEBI registration -- not merely whether the "
        "registration number looks valid.\n\n"
        "Holds no SEBI data. Domain facts are live RDAP; entity details come "
        "from the user reading SEBI's own listing, because SEBI blocks "
        "automated access and publishes no API."
    ),
)

# Open CORS for the prototype. Lock this down before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness check plus a description of what data this service uses."""
    # Touch the engine so a broken data path surfaces here rather than on the
    # first real request.
    get_engine()
    return HealthResponse(
        status="ok",
        mode="live",
        data_source=(
            "Domain registration data: live RDAP (public registry protocol). "
            "Entity details: from a SEBI listing file the operator imported, "
            "plus anything the user reads off SEBI's own listing. "
            "Nothing is fetched from SEBI."
        ),
        sebi_registry=registry_status(),
        disclaimer=(
            "This service holds no SEBI data it fetched itself and is not "
            "affiliated with SEBI. SEBI's portal is the authoritative source "
            "for registrations."
        ),
    )


@app.post("/verify", response_model=VerdictResponse, tags=["verify"])
def verify(payload: VerifyRequest) -> VerdictResponse:
    """Run a verification and return an explainable verdict.

    Nothing supplied here is persisted.
    """
    verdict = get_engine().verify(VerificationInput(**payload.model_dump()))
    return VerdictResponse(**verdict.to_dict())


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))
