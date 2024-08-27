import asyncio
import json
import logging
import os
import typing
import threading
import queue

import fastapi

import declare
import exception
import judge
import utils
from declare import JudgeSession, Language

# import zlib

Status = typing.Literal["busy", "idle", "disconnect"]
HEARTBEAT_INTERVAL = os.getenv("HEARTBEAT_INTERVAL", 3)
MSG_TIMEOUT = os.getenv("MSG_TIMEOUT", 5)


class SessionManager:
    logger: logging.Logger
    ws: fastapi.WebSocket = None
    status: declare.Status = declare.Status(status="disconnect")
    session: JudgeSession
    judge_abort: asyncio.Event = None  # noqa
    messages: asyncio.Queue = asyncio.Queue()
    stop_recv: asyncio.Event = asyncio.Event()
    judge_thread: threading.Thread = None

    def __init__(self) -> None:
        self.logger = logging.getLogger("judgyse.session")
        self.logger.addHandler(utils.console_handler("Session"))
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
        if self.ws is not None and self.ws.client_state != fastapi.websockets.WebSocketState.DISCONNECTED:
            try:
                await self.ws.close(*reason)
            except Exception as error:
                self.logger.error(error)

        self.clear("disconnect")
        self.logger.info("Disconnected")

    async def send(self, data: typing.Any):
        await asyncio.sleep(0)
        await self.ws.send_json(data)
        self.logger.debug(f"sent {data}")

    async def is_alive(self):
        while True:
            if self.stop_recv.is_set():
                return

            if self.ws is None or self.ws.client_state == fastapi.websockets.WebSocketState.DISCONNECTED:
                return await self.disconnect((1000, "client disconnected"))

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def recv(self):
        try:
            async for message in self.ws.iter_json():
                if self.stop_recv.is_set():
                    self.logger.info("stop recv")
                    break

                self.logger.debug(f"received {message}")

                command: str
                data: typing.Any

                command, data = utils.padding(message, 2)
                try:
                    data = json.loads(data)
                except (TypeError, json.decoder.JSONDecodeError):
                    pass

                if command == "close":
                    return await self.disconnect((1000, "client closed"))

                # elif command.startswith("ping"):
                #     await self.send(["pong", data])

                elif command.startswith("command."):
                    await self.handle(command[8:], data)

                elif command.startswith("declare."):
                    if data is not None and len(data) > 0:
                        data = json.loads(data[0])
                    self.declare(command[8:], data)

                else:
                    await self.messages.put([command, data])

        except fastapi.websockets.WebSocketDisconnect:
            return await self.disconnect()

        except exception.InvalidTestcaseIndex as error:
            await self.send(["judge.write:testcase",
                             {"status": 1, "code": "invalid_testcase_count",
                              "error": f"invalid testcase index: {error.args[0]}"}])

        except exception.MissingField as error:
            await self.send(["judge.write:code",
                             {"status": 1, "code": "missing_field",
                              "error": f"missing field: {error.args[0]}"}])

        except exception.InvalidField as error:
            await self.send(["judge.write:code",
                             {"status": 1, "code": "invalid_field",
                              "error": f"invalid field {error.args[0][0]}: "
                                       f"expected {error.args[0][1]}, got {error.args[0][2]}"}])

        except exception.CommandNotFound as error:
            await self.send(["unknown", str(error)])

        except Exception as error:
            raise error from error
            # await self.send(["error", str(error)])

    @staticmethod
    def declare(command: str, data: typing.Any) -> None:
        match command:
            case 'env':
                os.environ.update(data)

            case "language":
                utils.write_json(declare.judge.language_json, data)

            case "compiler":
                utils.write_json(declare.judge.compiler_json, data)

            case "load":
                declare.judge.load()

    async def handle(self, command: str, parsed: typing.Any) -> None:
        match command:
            case "start":
                self.status = declare.Status(status="busy")
                self.session: declare = {}
                self.judge_abort = asyncio.Event()
                utils.wipe_data(judge.execution_dir)
                utils.wipe_data(judge.testcases_dir)

            case "init":
                await self.parse_session(parsed)

            case "code":
                await self.write_code(parsed)

            case "judger":
                await self.write_judger(parsed)

            case "testcase":
                await self.write_testcase(parsed)

            case "judge":
                # async def job():
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
                            self.session.point,
                            self.judge_abort,
                    ):
                        if position == "compiler":
                            await self.send([
                                "judge.compiler",
                                str(data.get("message")),
                            ])

                        elif position == "overall":
                            await self.send(["judge.overall", status])

                        elif isinstance(position, int):
                            self.status = declare.Status(status="busy", progress=position.__str__())
                            await self.send(["judge.result", declare.JudgeResult(
                                position=position,
                                status=status,
                                error=data.get("error", None),
                                time=data.get("time", None),
                                memory=data.get("memory", None),
                                point=data.get("point", None),
                                feedback=data.get("feedback", None),
                            ).model_dump()])

                        else:
                            self.logger.error(f"unknown position: {position}")
                            self.logger.error(f"{position} {status} {data}")

                except exception.ABORTED:
                    self.logger.info("judge aborted")
                    await self.send(["judge.aborted"])

                except exception.COMPILE_ERROR as error:
                    self.logger.error("compile error, detail")
                    self.logger.exception(error)
                    await self.send([
                        "judge.error:compiler",
                        error.__str__(),
                    ])

                except exception.SYSTEM_ERROR as error:
                    self.logger.error("system error, detail")
                    self.logger.exception(error)
                    await self.send([
                        "judge.error:system",
                        error.__str__(),
                    ])

                except exception.UNKNOWN_ERROR as error:
                    # raise error from error
                    self.logger.error("unknown error, detail")
                    self.logger.exception(error)
                    await self.send([
                        "judge.error:system",
                        error.__str__(),
                    ])

                await self.send(["judge.done"])
                self.clear()

                # msg_queue = queue.Queue()
                # self.judge_thread = threading.Thread(
                #     target=judge.thread_judge,
                #     args=(
                #         self.session.submission_id,
                #         self.session.language,
                #         self.session.compiler,
                #         self.session.test_range,
                #         self.session.test_file,
                #         self.session.test_type,
                #         self.session.judge_mode,
                #         self.session.limit,
                #         self.session.point,
                #         self.judge_abort,
                #         msg_queue,
                #     ),
                #     name=f"judge-{self.session.submission_id}",
                # )
                # self.judge_thread.start()
                #
                # while self.judge_thread.is_alive() or not msg_queue.empty():
                #     try:
                #         position, status, data = await asyncio.to_thread(msg_queue.get, timeout=MSG_TIMEOUT)
                #
                #     except queue.Empty:
                #         continue
                #
                #     if position == "compiler":
                #         await self.send([
                #             "judge.compiler",
                #             str(data.get("message")),
                #         ])
                #
                #     elif position == "overall":
                #         await self.send(["judge.overall", status])
                #
                #     elif isinstance(position, int):
                #         self.status = declare.Status(status="busy", progress=position.__str__())
                #         await self.send(["judge.result", declare.JudgeResult(
                #             position=position,
                #             status=status,
                #             error=data.get("error", None),
                #             time=data.get("time", None),
                #             memory=data.get("memory", None),
                #             point=data.get("point", None),
                #             feedback=data.get("feedback", None),
                #         ).model_dump()])
                #
                #     else:
                #         self.logger.error(f"unknown position: {position}")
                #         self.logger.error(f"{position} {status} {data}")
                #
                # await self.send(["judge.done"])
                # self.clear()

            # case "abort":
            #     self.logger.debug("aborting judge")
            #
            #     if self.judge_abort:
            #         self.judge_abort.set()

            case "status":
                await self.send(["status", self.status.model_dump()])

            case _:
                raise exception.CommandNotFound(f"unknown command: {command}")

    async def parse_session(self, data: typing.Dict[str, typing.Any]) -> None:
        strict, optional = utils.get_fields(JudgeSession)

        for field in strict:
            if field not in data:
                raise exception.MissingField(field)

        submission_id = data["submission_id"]
        language = data["language"]
        compiler = data["compiler"]
        test_range = data["test_range"]
        test_file = data["test_file"]
        test_type = data["test_type"]
        judge_mode = data["judge_mode"]
        limit = data["limit"]
        point = data["point"]

        if not isinstance(submission_id, str):
            raise exception.InvalidField("submission_id", "str", type(submission_id))
        if not isinstance(language, list) or len(language) != 2:
            raise exception.InvalidField("language", "list(2)", type(language))
        if not isinstance(compiler, list) or len(compiler) != 2:
            raise exception.InvalidField("compiler", "list(2)", type(compiler))
        if not isinstance(test_range, list) or len(test_range) != 2:
            raise exception.InvalidField("test_range", "list(2)", type(test_range))
        if not isinstance(test_file, list) or len(test_file) != 2:
            raise exception.InvalidField("test_file", "list(2)", type(test_file))
        if not isinstance(test_type, str):
            raise exception.InvalidField("test_type", "str", type(test_type))
        if test_type not in ["file", "std"]:
            raise exception.InvalidField("test_type", "file, std", test_type)
        if not isinstance(judge_mode, dict):
            raise exception.InvalidField("judge_mode", "dict", type(judge_mode))
        if not isinstance(limit, dict):
            raise exception.InvalidField("limit", "dict", type(limit))
        if not isinstance(point, float):
            raise exception.InvalidField("point", "float", type(point))

        self.session = JudgeSession(
            submission_id=submission_id,
            language=tuple(language),
            compiler=tuple(compiler),
            test_range=tuple(test_range),
            test_file=tuple(test_file),
            test_type=test_type,
            judge_mode=declare.JudgeMode(**judge_mode),
            limit=declare.Limit(**limit),
            point=point,
        )

        await self.send(["judge.init", {"status": 0}])

    async def write_testcase(self, data: typing.Tuple[int, str, str, bool]) -> None:
        if data[0] not in range(
                self.session.test_range[0],
                self.session.test_range[1] + 1
        ):
            raise exception.InvalidTestcaseIndex(data[0])

        if not os.path.exists(os.path.join(judge.testcases_dir, str(data[0]))):
            os.makedirs(os.path.join(judge.testcases_dir, str(data[0])))

        input_content = data[1]
        output_content = data[2]
        # compressed = data[3]
        # if compressed:
        #     input_content = zlib.decompress(input_content)
        #     output_content = zlib.decompress(output_content)

        utils.write(
            os.path.join(judge.testcases_dir, str(data[0]), self.session.test_file[0]),
            input_content,
        )
        utils.write(
            os.path.join(judge.testcases_dir, str(data[0]), self.session.test_file[1]),
            output_content,
        )
        await self.send(["judge.write:testcase", {"status": 0, "index": data[0]}])

    async def write_code(self, data: typing.Tuple[str, bool]) -> None:
        file_name = Language[self.session.language[0]].file.format(
            id=self.session.submission_id
        )
        file_content = data[0]
        # compressed = data[1]
        # if compressed:
        #     file_content = zlib.decompress(file_content)
        utils.write(os.path.join(judge.execution_dir, file_name), file_content)

        await self.send(["judge.write:code", {"status": 0}])

    async def write_judger(self, data: typing.Tuple[str, bool]) -> None:
        file_content = data[0]
        # compressed = data[1]
        # if compressed:
        #     file_content = zlib.decompress(file_content)
        utils.write(os.path.join(judge.execution_dir, "judger.py"), file_content)

        await self.send(["judge.write:judger", {"status": 0}])
