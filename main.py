import os
import secrets
import threading
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
import agents
import engine

security = HTTPBasic(auto_error=False)


def operator(credentials: Annotated[HTTPBasicCredentials | None, Depends(security)]):
    password = os.getenv("OPERATOR_PASSWORD", "")
    if not password and os.getenv("APP_ENV", "local") == "local":
        return "local-operator"
    if not password or not credentials or not (
        secrets.compare_digest(credentials.username.encode(), b"operator") and
        secrets.compare_digest(credentials.password.encode(), password.encode())
    ):
        raise HTTPException(401, "Operator sign-in required", headers={"WWW-Authenticate": 'Basic realm="Hyatus Ops"'})
    return "operator"


@asynccontextmanager
async def lifespan(app):
    if os.getenv("APP_ENV", "local") != "local" and len(os.getenv("OPERATOR_PASSWORD", "")) < 16:
        raise RuntimeError("Configure an operator password of at least 16 characters before serving outside local mode")
    if not agents.MOCK:
        agents.model()  # Missing key/model fails startup instead of silently using mock decisions.
    engine.init()
    stop = threading.Event()
    thread = threading.Thread(target=engine.worker, args=(stop,), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=30)


app = FastAPI(title="Hyatus Ops", lifespan=lifespan, docs_url=None, redoc_url=None)
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Text
    source: str = Field(default="operator", min_length=1, max_length=40)


class HumanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["close", "redispatch"]
    note: Text


@app.middleware("http")
async def boundaries(request: Request, call_next):
    from fastapi.responses import JSONResponse
    if request.method == "POST":
        # JSON API is never a cross-origin form target.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in (str(request.base_url).rstrip("/"), os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"), "http://127.0.0.1:3000"):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 16384:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            # Cache a bounded body so the downstream parser can read it.
            request._body = getattr(request, "_body", b"") + chunk
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.post("/api/events", status_code=201)
def post_event(e: Event, actor=Depends(operator), idempotency_key: Annotated[str | None, Header(max_length=100)] = None):
    try:
        return {"incident_id": engine.create_incident(e.message, e.source, idempotency_key)}
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except OverflowError as error:
        raise HTTPException(503, str(error), headers={"Retry-After": "5"}) from None


@app.get("/api/state")
def state(actor=Depends(operator)):
    return engine.snapshot()


@app.post("/api/incidents/{iid}/human")
def human(iid: str, a: HumanAction, actor=Depends(operator)):
    try:
        ok = engine.human_action(iid, a.action, a.note, actor)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if not ok:
        raise HTTPException(409, "Incident is no longer awaiting review; refresh and try again")
    return {"ok": True}


@app.get("/api/incidents/{iid}")
def incident_detail(iid: str, actor=Depends(operator)):
    with engine.conn() as c:
        row = c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
        if not row:
            raise HTTPException(404, "Request not found")
        return {"incident": dict(row), "events": [dict(e) for e in c.execute(
            "SELECT * FROM events WHERE incident_id=? ORDER BY id DESC LIMIT 100", (iid,))]}


@app.get("/")
def health():
    return {"service": "hyatus-ops", "status": "ok"}
