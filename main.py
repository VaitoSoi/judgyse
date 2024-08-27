import asyncio
import logging
import os
from contextlib import asynccontextmanager

import fastapi
from fastapi.responses import HTMLResponse

import utils
from session import SessionManager

# print(os.environ)

session_manager = SessionManager()

main_logger = logging.getLogger("judgyse.main")
main_logger.addHandler(utils.console_handler("Main"))
judgyse_logger = logging.getLogger("judgyse")
judgyse_logger.propagate = False
judgyse_logger.setLevel(os.getenv("LOG_LEVEL", "DEBUG"))

uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.handlers.clear()
uvicorn_error_logger = logging.getLogger("uvicorn.error")
uvicorn_error_logger.handlers.clear()
uvicorn_error_logger.addHandler(utils.console_handler("Uvicorn"))
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.handlers.clear()
uvicorn_access_logger.addHandler(utils.console_handler("Access", utils.AccessFormatter))

fastapi_logger = logging.getLogger("fastapi")
fastapi_logger.handlers.clear()
fastapi_logger.addHandler(utils.console_handler("FastAPI"))


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    yield
    if session_manager.status.status != 'disconnect':
        session_manager.stop_recv.set()
        await session_manager.disconnect()


app = fastapi.FastAPI(
    title="Judgyse Server",
    lifespan=lifespan
)


@app.websocket("/session")
async def session(ws: fastapi.WebSocket):
    await ws.accept()
    if session_manager.status.status != "disconnect":
        main_logger.debug("busy")
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
