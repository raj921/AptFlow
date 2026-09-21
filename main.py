import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import engine


@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    t = threading.Thread(target=engine.worker, args=(stop,), daemon=True)
    t.start()
    yield
    stop.set()


app = FastAPI(title="hyatus-ops", lifespan=lifespan)


class Event(BaseModel):
    message: str
    source: str = "guest"


class HumanAction(BaseModel):
    action: str  # "close" | "redispatch"
    note: str = ""


@app.post("/api/events")
def post_event(e: Event):
    return {"incident_id": engine.create_incident(e.message, e.source)}


@app.get("/api/state")
def state():
    return engine.snapshot()


@app.post("/api/incidents/{iid}/human")
def human(iid: str, a: HumanAction):
    ok = engine.human_action(iid, a.action, a.note)
    return {"ok": ok}


@app.get("/")
def dashboard():
    return FileResponse("static/index.html")
