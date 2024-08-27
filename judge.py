import ast
import asyncio
import logging
import os
import queue
import shlex
import shutil
import subprocess
import sys
import typing

import docker
import docker.errors
import docker.models
import docker.models.containers
import requests
import urllib3

import declare
import utils
from exception import (
    ABORTED,
    MEMORYLIMIT_EXCEEDED,
    TIMELIMIT_EXCEEDED,
    COMPILE_ERROR,
    SYSTEM_ERROR,
    RUNTIME_ERROR,
    UNKNOWN_ERROR,
    JUDGER_ERROR
)

__all__ = [
    "judge_dir",
    "execution_dir",
    "testcases_dir",
    "DockerClient",
    "judge"
]

INSIDE_DOCKER = os.getenv("INSIDE_DOCKER", None) == "1"
RUN_IN_DOCKER = os.getenv("RUN_IN_DOCKER", None) == "1"  # or INSIDE_DOCKER
if sys.platform == "nt" and not RUN_IN_DOCKER:
    raise Exception("Windows is not supported, use Docker instead")

DockerClient: docker.DockerClient = None
if RUN_IN_DOCKER or INSIDE_DOCKER:
    try:
        DockerClient = docker.from_env()
    except docker.errors.DockerException as error:
        if error.__str__().startswith("Error while fetching server API version"):
            raise Exception("Cannot connect to Docker daemon, is it running ?")
        else:
            raise error

process_id: str = None
judge_dir: str = None

if INSIDE_DOCKER:
    HOSTNAME = os.getenv("HOSTNAME", None)
    process_id = DockerClient.api.inspect_container(HOSTNAME)["Name"].split("_")[-1]
    judge_dir = os.path.join(os.path.abspath("evaluation"), process_id[1:])
else:
    judge_dir = os.path.abspath("evaluation")

execution_dir = os.path.join(judge_dir, "execution")
testcases_dir = os.path.join(judge_dir, "testcases")
WIPE = os.getenv("WIPE", None) == "1"
if WIPE:
    utils.wipe_data(execution_dir)
    utils.wipe_data(testcases_dir)
else:
    if not os.path.exists(execution_dir):
        os.makedirs(execution_dir)

    if not os.path.exists(testcases_dir):
        os.makedirs(testcases_dir, exist_ok=True)

HARD_LIMIT = os.getenv("HARD_LIMIT", None) == "1"
COMPILER_MEM_LIMIT = os.getenv("COMPILER_MEM_LIMIT", "1024m")
TIME_PATH = os.getenv("TIME_PATH", None)
TIMEOUT_PATH = os.getenv("TIMEOUT_PATH", None)
if HARD_LIMIT:
    if not os.path.exists(TIME_PATH):
        raise Exception(f"{TIME_PATH} not found")
    if not os.path.exists(TIMEOUT_PATH):
        raise Exception(f"{TIMEOUT_PATH} not found")

stt = utils.str_to_timestamp
mem_parse = utils.mem_convert
wrap = utils.wrap_dict

logger = logging.getLogger("judgyse.judge")
logger.addHandler(utils.console_handler("Judge"))


def thread_judge(
        submission_id: str,
        language: typing.Tuple[str, typing.Optional[int]],
        compiler: typing.Tuple[str, typing.Union[typing.Literal["latest"], str]],
        test_range: typing.Tuple[int, int],
        test_file: typing.Tuple[str, str],
        test_type: typing.Literal["file", "std"],
        judge_mode: declare.JudgeMode,
        limit: declare.Limit,
        point_per_testcase: float,
        abort: asyncio.Event,
        msg_queue: queue.Queue
):
    try:
        for data in judge(submission_id,
                          language,
                          compiler,
                          test_range,
                          test_file,
                          test_type,
                          judge_mode,
                          limit,
                          point_per_testcase,
                          abort):
            msg_queue.put(data)

    except ABORTED:
        msg_queue.put([
            "system",
            "aborted",
        ])

    except COMPILE_ERROR as error:
        # self.logger.error("compile error, detail")
        # self.logger.exception(error)
        msg_queue.put((
            "compiler",
            declare.StatusCode.COMPILE_ERROR.value,
            error.__str__(),
        ))

    except SYSTEM_ERROR as error:
        msg_queue.put([
            "system",
            declare.StatusCode.SYSTEM_ERROR.value,
            error.__str__(),
        ])

    except UNKNOWN_ERROR as error:
        # raise error from error
        msg_queue.put([
            "system",
            declare.StatusCode.UNKNOWN_ERROR.value,
            error.__str__(),
        ])


def judge(
        submission_id: str,
        language: typing.Tuple[str, typing.Optional[int]],
        compiler: typing.Tuple[str, typing.Union[typing.Literal["latest"], str]],
        test_range: typing.Tuple[int, int],
        test_file: typing.Tuple[str, str],
        test_type: typing.Literal["file", "std"],
        judge_mode: declare.JudgeMode,
        limit: declare.Limit,
        point_per_testcase: float,
        abort: asyncio.Event
) -> typing.Iterator[
    tuple[typing.Literal["compiler", "system"] | int, declare.StatusCode, dict[str, str | int] | None]
]:
    file = declare.Language[language[0]]
    code = file.file.format(id=submission_id)
    executable = file.executable.format(id=submission_id)

    command = declare.Compiler[compiler[0]]
    image = command.image.format(version=compiler[1])
    compile = command.compile.format(
        source=code,
        executable=executable,
        version=language[1]
    )
    execute = command.execute.format(executable=executable)
    print(compile, execute)

    """
    Compile
    """

    try:
        warn: str = None
        if not RUN_IN_DOCKER or INSIDE_DOCKER:
            command = (
                f"ulimit -v {mem_parse(COMPILER_MEM_LIMIT)} && {compile}"
                if HARD_LIMIT
                else compile
            )

            callback = subprocess.run(
                command.split(),
                cwd=execution_dir,
                capture_output=True,
                check=True,
            )
            warn = callback.stdout.decode()

        else:
            warn: bytes = DockerClient.containers.run(
                image=image,
                command=compile,
                detach=False,
                stdout=True,
                stderr=True,
                volumes=[f"{execution_dir}:/compile"],
                working_dir="/compile",
                mem_limit=COMPILER_MEM_LIMIT,
            )
            warn = warn.decode()

        if warn:
            yield "compiler", "warn", {"message": warn}

    except (docker.errors.ContainerError, subprocess.CalledProcessError) as e:
        raise COMPILE_ERROR(*e.args) from e

    except docker.errors.APIError as e:
        raise SYSTEM_ERROR(*e.args) from e

    except Exception as e:
        raise UNKNOWN_ERROR(*e.args) from e

    """
    Execute
    """

    results: typing.List[declare.JudgeResult] = []

    def save(*data: typing.Any):
        data = utils.padding(data, 3, {})
        results.append(data)
        yield data

    for i in range(test_range[0], test_range[1] + 1, 1):
        if abort.is_set():
            logger.debug("Aborted")
            raise ABORTED()

        time: float = -1
        memory: tuple[int, int] = [-1, -1]
        output = ""
        expect = utils.read(os.path.join(testcases_dir, str(i), test_file[1]))

        command = f"{{timeout}} {execute}"
        if test_type == "std":
            command = f'cat {test_file[0]} | {execute}'

        if HARD_LIMIT:
            command = f'ulimit -v {mem_parse(limit.memory)} && /bin/bash -c "{command}"'

        else:
            command = f'/bin/bash -c "{command}"'

        _execution_dir = execution_dir
        _testcases_dir = testcases_dir
        if INSIDE_DOCKER:
            JUDGYSE_DIR = os.getenv("JUDGYSE_DIR", "/judgyse")
            _execution_dir = os.path.join(JUDGYSE_DIR, *execution_dir.split("/")[2:])
            _testcases_dir = os.path.join(JUDGYSE_DIR, *testcases_dir.split("/")[2:])

        if RUN_IN_DOCKER:
            command = f'/usr/bin/time --format="--judgyse_static:amemory=%K,pmemory=%M,return=%x" ' \
                      f'{command.format(timeout="")}'

        else:
            command = \
                f'{TIME_PATH or "/usr/bin/time"} --format="--judgyse_static:time=%e,amemory=%K,pmemory=%M,return=%x" ' \
                f'{command.format(timeout=f"{TIMEOUT_PATH or "/usr/bin/timeout"} {limit.time}")}'

        try:
            if not RUN_IN_DOCKER or INSIDE_DOCKER:
                shutil.copyfile(
                    os.path.join(testcases_dir, str(i), test_file[0]),
                    os.path.join(_execution_dir, test_file[0])
                )
                callback = subprocess.run(
                    shlex.split(command),
                    cwd=_execution_dir,
                    capture_output=True,
                    timeout=limit.time,
                    check=True,
                )
                _output = callback.stdout.decode()
                statics = callback.stderr.decode().split('--judgyse_static:')[-1][:-1]

                statics = wrap([tuple(static.split("=")) for static in statics.split(",")])
                time = float(statics["time"])
                memory = (int(statics["amemory"]) / 1024, int(statics["pmemory"]) / 1024)
                if memory[1] > mem_parse(limit.memory):
                    raise MEMORYLIMIT_EXCEEDED()
                return_code = int(statics["return"])

            else:
                # print(command)
                container: docker.models.containers.Container = DockerClient.containers.run(
                    image=image,
                    command=command,
                    detach=True,
                    mem_limit=limit.memory,
                    network_disabled=True,
                    working_dir="/execution",
                    volumes=[
                        f"{_execution_dir}:/execution",
                        f"{_testcases_dir}/{i}/{test_file[0]}:/execution/{test_file[0]}",
                        *([f"{TIME_PATH}:/usr/bin/time"] if TIME_PATH else []),
                    ]
                )
                container.wait(timeout=limit.time)
                inspect = DockerClient.api.inspect_container(container.id)

                if str(inspect["State"]["OOMKilled"]).lower() == "true":
                    raise MEMORYLIMIT_EXCEEDED()

                log = container.logs(stdout=True, stderr=True).decode("utf-8")
                _output, statics = log.split("--judgyse_static:")

                state = inspect["State"]
                statics = wrap(
                    [tuple(static.split("=")) for static in statics[:-1].split(",")]
                )

                time = stt(state["FinishedAt"]) - stt(state["StartedAt"])
                memory = (int(statics["amemory"]) / 1024, int(statics["pmemory"]) / 1024)
                return_code = int(statics["return"])

                container.remove()

            if return_code != 0:
                raise RUNTIME_ERROR(_output)

            if test_type == "file":
                output = utils.read(os.path.join(execution_dir, test_file[1]))

            else:
                output = _output

        except RUNTIME_ERROR as e:
            yield from save(i, declare.StatusCode.RUNTIME_ERROR.value, str(e.args[0]))
            continue

        except MEMORYLIMIT_EXCEEDED:
            yield from save(i, declare.StatusCode.MEMORY_LIMIT_EXCEEDED.value)
            continue

        except (TIMELIMIT_EXCEEDED, subprocess.TimeoutExpired):
            yield from save(i, declare.StatusCode.TIME_LIMIT_EXCEEDED.value)
            continue

        except requests.exceptions.ConnectionError as e:
            if urllib3.exceptions.ReadTimeoutError in e.args:
                yield from save(i, declare.StatusCode.TIME_LIMIT_EXCEEDED.value, str(e))
                continue

            else:
                raise SYSTEM_ERROR(*e.args) from e
        except subprocess.TimeoutExpired:
            yield from save(i, declare.StatusCode.TIME_LIMIT_EXCEEDED.value)
            continue

        except (
                docker.errors.ContainerError,
                docker.errors.APIError,
                subprocess.CalledProcessError
        ) as error:
            # raise error
            raise SYSTEM_ERROR(*error.args) from error

        except Exception as e:
            raise e from e
            # raise UNKNOWN_ERROR(*e.args) from e

        status: int = None
        point = 0
        feedback = None
        if judge_mode.mode == 0:
            a = output
            b = expect
            if judge_mode.trim_endl:
                a = "\n".join([a for a in a.split("\n") if a])
                b = "\n".join([b for b in b.split("\n") if b])
            if judge_mode.case:
                a = a.lower()
                b = b.lower()
            comp = a == b
            point = point_per_testcase if comp else 0
            status = declare.StatusCode.ACCEPTED.value if comp else declare.StatusCode.WRONG_ANSWER.value
            feedback = "Accepted :D" if comp else output

        elif judge_mode.mode == 1:
            command = (f'python -c "import main from judger; '
                       f'print(main({output}, {expect}, '
                       f'{{"index": {i}, "point": {point_per_testcase}, "language": "{language[0]}", '
                       f'"time": {time}, "memory": {memory}}}))"')
            judger_output = None
            try:
                if RUN_IN_DOCKER:
                    judger_output = DockerClient.containers.run(
                        image="python:latest",
                        command=command,
                        detach=False,
                        network_disabled=False,
                        working_dir="/execution",
                        volumes=[f"{execution_dir}:/execution"],
                    ).decode()
                else:
                    judger_output = subprocess.run(
                        command.split(),
                        capture_output=True,
                        check=True,
                        cwd=execution_dir
                    ).stdout.decode()

            except (subprocess.CalledProcessError,
                    docker.errors.ContainerError) as error:
                raise JUDGER_ERROR(*error.args) from error

            except docker.errors.APIError as error:
                raise SYSTEM_ERROR(*error.args) from error

            judger_output = ast.literal_eval(judger_output)
            if isinstance(judger_output, bool):
                status = declare.StatusCode.ACCEPTED.value if judger_output else declare.StatusCode.WRONG_ANSWER.value
                point = point_per_testcase if judger_output else 0
                feedback = "Accepted :D" if judger_output else output

            elif isinstance(judger_output, dict):
                status = judger_output.get("status", None)
                point = judger_output.get("point", None)
                feedback = judger_output.get("feedback", "Accepted :D" if status == 0 else output)
                if status is None or point is None:
                    raise JUDGER_ERROR("Invalid output from judger")

        yield from save(
            i,
            status,
            {"time": time, "memory": memory, "point": point, "feedback": feedback},
        )

    results.sort(reverse=True, key=lambda x: x[1])
    judge_status = results[0]

    yield "overall", judge_status[1], {}
