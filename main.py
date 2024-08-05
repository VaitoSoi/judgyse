import fastapi
import logging
import asyncio
from fastapi.responses import HTMLResponse
from session import SessionManager
from contextlib import asynccontextmanager

session_manager = SessionManager()
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    yield
    if session_manager.status[0] != 'disconnect':
        session_manager.stop_recv.set()
        await session_manager.disconnect()
        # await task


app = fastapi.FastAPI(lifespan=lifespan)


@app.websocket("/session")
async def session(ws: fastapi.WebSocket):
    await ws.accept()
    if session_manager.status[0] != "disconnect":
        print("busy")
        return await ws.close(fastapi.status.WS_1013_TRY_AGAIN_LATER, "busy")
    session_manager.connect(ws)
    await asyncio.gather(session_manager.recv(), session_manager.is_alive())


@app.get("/status", tags=["status"])
async def status(response: HTMLResponse):
    if session_manager != "disconnect":
        return {"status": session_manager.status}
    else:
        response.status_code = fastapi.status.HTTP_503_SERVICE_UNAVAILABLE
        return "no session is running"


@app.get("/is_judging", tags=["status"])
async def is_judging():
    if session_manager.status == "disconnect":
        return {"is_judging": None}
    return {
        "is_judging": session_manager.judge_thread.is_alive() if session_manager.judge_thread else False
    }
