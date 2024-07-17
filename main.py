import fastapi
from session import SessionManager
from contextlib import asynccontextmanager

session = SessionManager()


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    yield
    await session.stop()


app = fastapi.FastAPI(lifespan=lifespan)


@app.websocket("/jugde")
async def jugde(ws: fastapi.WebSocket):
    await ws.accept()
    await session.start(ws)


@app.get("/status", tags=["status"])
async def status():
    return {"status": session.status}


@app.get("/is_judging", tags=["status"])
async def is_judging():
    return {
        "is_judging": session.jugde_thread.is_alive() if session.jugde_thread else False
    }
