import fastapi
import typing
import os
import threading
import zlib
import judge
import utils
import json
import shutil
import queue
import asyncio
from declare import JudgeSession

class SessionManager:
    ws: fastapi.WebSocket = None
    status: typing.Literal["busy", "idle"] = "idle"
    session: JudgeSession = {}
    jugde_thread: threading.Thread = None
    thread_abort: threading.Event = None

    async def start(self, ws: fastapi.WebSocket) -> None:
        self.ws = ws
        self.status = "busy"
        self.session = {}
        self.jugde_thread = None
        self.thread_abort = threading.Event()

        while self.status == "busy":
            try:
                data = await self.ws.receive_json()
                await self.handle(data)
            except fastapi.websockets.WebSocketDisconnect:
                return await self.stop()
            # except Exception as e:
            #     print(e)
            #     await self.ws.send_json({"error": str(e)})
            #     await self.stop()

    async def stop(self) -> None:
        if self.thread_abort:
            self.thread_abort.set()
        if self.jugde_thread:
            self.jugde_thread.join()
        try:
            await self.ws.close()
        except Exception:
            pass
        self.ws = None
        self.status = "idle"
        self.session = {}
        self.jugde_thread = None
        self.thread_abort = None

    async def handle(self, data: typing.Dict[str, typing.Any]) -> None:
        command = data[0]
        try:
            parsed = json.loads(data[1])
        except (TypeError, json.decoder.JSONDecodeError):
            parsed = data[1]

        if command == "init":
            shutil.rmtree(judge.judge_dir, ignore_errors=True)
            os.makedirs(judge.testcases_dir, exist_ok=True)
            os.makedirs(judge.judge_dir, exist_ok=True)
            os.makedirs(judge.execution_dir, exist_ok=True)
            self.parse_session(parsed)

        elif command == "code":
            self.write_code(parsed)

        elif command == "testcase":
            self.write_testcase(parsed)

        elif command == "judge":
            msg_queue = queue.Queue()
            self.jugde_thread = threading.Thread(
                target=judge.judge,
                args=(
                    self.session.submission_id,
                    self.session.language,
                    self.session.compiler,
                    self.session.test_range,
                    self.session.test_file,
                    self.session.test_type,
                    self.session.judge_mode,
                    self.session.limit,
                    self.ws,
                    self.thread_abort,
                    msg_queue
                ),
            )
            self.jugde_thread.start()
            while self.jugde_thread.is_alive():
                msg = msg_queue.get()
                await asyncio.sleep(0)
                await self.ws.send_json(msg)
            await self.stop()
            
        elif command =="arbort":
            if not self.thread_abort:
                return self.ws.send_json({"error": "No active session"})
            self.stop()

        elif command == "status":
            self.ws.send_json({"status": self.status})

    def parse_session(self, data: typing.Dict[str, typing.Any]) -> None:
        strict, optional = utils.get_fields(JudgeSession)

        for field in strict:
            if field not in data:
                raise ValueError(f"Missing field: {field}")

        self.session = JudgeSession(
            submission_id=data["submission_id"],
            language=(data["language"][0], data["language"][1]),
            compiler=(data["compiler"][0], data["compiler"][1]),
            test_range=(data["test_range"][0], data["test_range"][1]),
            test_file=(data["test_file"][0], data["test_file"][1]),
            test_type=data["test_type"],
            judge_mode=judge.JudgeMode(**data["judge_mode"]),
            limit=judge.Limit(**data["limit"]),
        )

    def write_testcase(self, data: typing.Tuple[int, str, str, bool]) -> None:
        if data[0] not in range(self.session.test_range[0], self.session.test_range[1] + 1):
            raise ValueError(f"Invalid test case index: {data[0]}")

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

    def write_code(self, data: typing.Tuple[str, bool]) -> None:
        file_name = judge.File[self.session.language[0]].file.format(
            id=self.session.submission_id
        )
        file_content = data[0]
        compressed = data[1]
        if compressed:
            file_content = zlib.decompress(file_content)
        utils.write(os.path.join(judge.execution_dir, file_name), file_content)
