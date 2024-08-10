import asyncio
import json
import logging
import os
import typing
import zlib

import fastapi
import judge

import declare
import exception
import utils
from declare import JudgeSession, Language

Status = typing.Literal["busy", "idle", "disconnect"]
HEARTBEAT_INTERVAL = os.getenv("HEARTBEAT_INTERVAL", 3)


class SessionManager:
    ws: fastapi.WebSocket = None
    status: declare.Status = declare.Status(status="idle")
    session: JudgeSession
    judge_abort: utils.Event = None  # noqa
    logger: logging.Logger = logging.getLogger("uvicorn.error")
    messages: asyncio.Queue = asyncio.Queue()
    stop_recv: asyncio.Event = asyncio.Event()

    def __init__(self) -> None:
        pass

    def connect(self, ws: fastapi.WebSocket) -> None:
        self.ws = ws
        self.clear()
        self.stop_recv.clear()

    def clear(self, status: Status = "idle"):
        if self.judge_abort:
            self.judge_abort.set()

        self.status = declare.Status(status=status)
        self.session = {}
        self.judge_abort = None

    async def disconnect(self, reason: tuple[int, str | None] = (1000,)) -> None:
        self.stop_recv.set()
        if (
                self.ws is not None
                and self.ws.client_state != fastapi.websockets.WebSocketState.DISCONNECTED
        ):
            try:
                await self.ws.close(*reason)
            except Exception as error:
                self.logger.error(error)

        self.clear("disconnect")
        self.logger.info("Disconnected")

    async def send(self, data: typing.Any):
        await asyncio.sleep(0)
        await self.ws.send_json(data)
        self.logger.info(f"sent {data}")

    async def is_alive(self):
        while True:
            if self.stop_recv.is_set():
                return

            if (
                    self.ws is None
                    or self.ws.client_state == fastapi.websockets.WebSocketState.DISCONNECTED
            ):
                return await self.disconnect((1000, "client disconnected"))

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def recv(self):
        try:
            async for message in self.ws.iter_json():
                if self.stop_recv.is_set():
                    self.logger.info("stop recv")
                    break

                self.logger.info(f"received {message}")

                command: str
                data: typing.Any

                command, data = utils.padding(message, 2)
                try:
                    data = json.loads(data)
                except (TypeError, json.decoder.JSONDecodeError):
                    pass

                if command == "close":
                    return await self.disconnect((1000, "client closed"))

                if command.startswith("ping"):
                    await self.send(["pong", data])

                elif command.startswith("command."):
                    await self.handle(command[8:], data)

                elif command.startswith("declare."):
                    if data is not None and len(data) > 0:
                        data = json.loads(data[0])

                    match command[8:]:
                        case 'env':
                            os.environ.update(data)

                        case "language":
                            utils.write_json(declare.judge.language_json, data)

                        case "compiler":
                            utils.write_json(declare.judge.compiler_json, data)

                        case "load":
                            declare.judge.load()

                else:
                    await self.messages.put([command, data])

        except fastapi.websockets.WebSocketDisconnect:
            return await self.disconnect()

        except Exception as error:
            raise error from error
            # await self.send(["error", str(error)])

    async def handle(self, command: str, parsed: typing.Any) -> None:
        match command:
            case "start":
                self.status = declare.Status(status="busy")
                self.session: declare = {}
                self.judge_abort = utils.Event()

            case "init":
                await self.parse_session(parsed)

            case "code":
                await self.write_code(parsed)

            case "testcase":
                await self.write_testcase(parsed)

            case "judge":
                try:
                    for position, status, data in judge.judge(
                            self.session.submission_id,
                            self.session.language,
                            self.session.compiler,
                            self.session.test_range,
                            self.session.test_file,
                            self.session.test_type,
                            self.session.judge_mode,
                            self.session.limit,
                            self.judge_abort,
                    ):
                        if isinstance(position, int):
                            self.status = declare.Status(status="busy", progress=position)

                        result = declare.JudgeResult(
                            position=position,
                            status=status,
                            warn=data.get("warn"),
                            time=data.get("time"),
                            memory=data.get("memory"),
                        ).model_dump()
                        await self.send(["judge.result", result])

                except exception.ABORTED:
                    await self.send(["judge.aborted"])

                except exception.COMPILE_ERROR as error:
                    await self.send(
                        [
                            "judge.error",
                            declare.JudgeResult(
                                position="compiler",
                                status=declare.StatusCode.COMPILE_ERROR,
                                error=str(error),
                            ).model_dump(),
                        ]
                    )

                except exception.SYSTEM_ERROR as error:
                    await self.send(
                        [
                            "judge.error",
                            declare.JudgeResult(
                                position="system",
                                status=declare.StatusCode.SYSTEM_ERROR,
                                error=str(error),
                            ).model_dump(),
                        ]
                    )

                except exception.UNKNOWN_ERROR as error:
                    # raise error from error
                    self.logger.error(error)
                    await self.send(
                        [
                            "judge.error",
                            declare.JudgeResult(
                                position="system",
                                status=declare.StatusCode.SYSTEM_ERROR,
                                error=str(error),
                            ).model_dump(),
                        ]
                    )

                await self.send(["judge.done"])
                self.clear()

            case "abort":
                self.judge_abort.set()

            case "status":
                await self.send(["status", self.status.model_dump()])

            case _:
                raise exception.CommandNotFound(f"unknown command: {command}")

    async def parse_session(self, data: typing.Dict[str, typing.Any]) -> None:
        strict, optional = utils.get_fields(JudgeSession)

        for field in strict:
            if field not in data:
                raise exception.MissingField(f"missing field: {field}")

        self.session = JudgeSession(
            submission_id=data["submission_id"],
            language=(data["language"][0], data["language"][1]),
            compiler=(data["compiler"][0], data["compiler"][1]),
            test_range=(data["test_range"][0], data["test_range"][1]),
            test_file=(data["test_file"][0], data["test_file"][1]),
            test_type=data["test_type"],
            judge_mode=declare.JudgeMode(**data["judge_mode"]),
            limit=declare.Limit(**data["limit"]),
        )

        await self.send(["judge.initialized"])

    async def write_testcase(self, data: typing.Tuple[int, str, str, bool]) -> None:
        if data[0] not in range(
                self.session.test_range[0], self.session.test_range[1] + 1
        ):
            raise exception.InvalidTestcaseIndex(data[0])

        if not os.path.exists(os.path.join(judge.testcases_dir, str(data[0]))):
            os.makedirs(os.path.join(judge.testcases_dir, str(data[0])))

        input_content = data[1]
        output_content = data[2]
        compressed = data[3]
        if compressed:
            input_content = zlib.decompress(input_content)
            output_content = zlib.decompress(output_content)

        utils.write(
            os.path.join(judge.testcases_dir, str(data[0]), self.session.test_file[0]),
            input_content,
        )
        utils.write(
            os.path.join(judge.testcases_dir, str(data[0]), self.session.test_file[1]),
            output_content,
        )
        await self.send(["judge.written:testcase", data[0]])

    async def write_code(self, data: typing.Tuple[str, bool]) -> None:
        file_name = Language[self.session.language[0]].file.format(
            id=self.session.submission_id
        )
        file_content = data[0]
        compressed = data[1]
        if compressed:
            file_content = zlib.decompress(file_content)
        utils.write(os.path.join(judge.execution_dir, file_name), file_content)

        await self.send(["judge.written:code"])
