import os
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
try:
    from . import utils
    from .exception import (
        ABORTED,
        MEMORYLIMIT_EXCEEDED,
        TIMELIMIT_EXCEEDED,
        COMPILE_ERROR,
        SYSTEM_ERROR,
        RUNTIME_ERROR,
        UNKNOWN_ERROR,
    )
except ImportError:
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
    )

__all__ = [
    "judge_dir",
    "execution_dir",
    "testcases_dir",
    "DockerClient",
    "judge"
]

INSIDE_DOCKER = os.getenv("INSIDE_DOCKER", None) == "1"
RUN_IN_DOCKER = INSIDE_DOCKER or os.getenv("RUN_IN_DOCKER", None) == "1"
if sys.platform == "nt" and not RUN_IN_DOCKER:
    raise Exception("Windows is not supported, use Docker instead")

process_id: str = None
judge_dir: str = None

DockerClient: docker.DockerClient = None
if RUN_IN_DOCKER:
    try:
        DockerClient = docker.from_env()
    except docker.errors.DockerException as error:
        if error.__str__().startswith("Error while fetching server API version"):
            raise Exception("Cannot connect to Docker daemon, is it running ?")
        else:
            raise error

if INSIDE_DOCKER:
    HOSTNAME = os.getenv("HOSTNAME", None)
    process_id = DockerClient.api.inspect_container(HOSTNAME)["Name"].split("_")[-1]
    judge_dir = os.path.join(os.path.abspath("judge"), process_id[1:])
else:
    judge_dir = os.path.abspath("judge")

HARD_LIMIT = os.getenv("HARD_LIMIT", None) == "1"
COMPILER_MEM_LIMIT = os.getenv("COMPILER_MEM_LIMIT", "1024m")
TIME_PATH = os.getenv("TIME_PATH", "/usr/bin/time")
TIMEOUT_PATH = os.getenv("TIMEOUT_PATH", "/usr/bin/timeout")
if HARD_LIMIT:
    if not os.path.exists(TIME_PATH):
        raise Exception(f"{TIME_PATH} not found")
    if not os.path.exists(TIMEOUT_PATH):
        raise Exception(f"{TIMEOUT_PATH} not found")

execution_dir = os.path.join(judge_dir, "execution")
testcases_dir = os.path.join(judge_dir, "testcases")

stt = utils.str_to_timestamp
mem_parse = utils.mem_convert
wrap = utils.wrap_dict


def judge(
        submission_id: str,
        language: typing.Tuple[str, typing.Optional[int]],
        compiler: typing.Tuple[str, typing.Union[typing.Literal["latest"], str]],
        test_range: typing.Tuple[int, int],
        test_file: typing.Tuple[str, str],
        test_type: typing.Literal["file", "std"],
        judge_mode: declare.JudgeMode,
        limit: declare.Limit,
        abort: utils.Event,
        _judge_dir: str = None,
) -> typing.Iterator[
    tuple[typing.Literal["compile", "overall"] | int, str, dict[str, str | int] | None]
]:  # [position, status, data]
    global judge_dir
    if _judge_dir:
        judge_dir = _judge_dir

    execution_dir = os.path.join(judge_dir, "execution")
    testcases_dir = os.path.join(judge_dir, "testcases")
    os.makedirs(testcases_dir, exist_ok=True)
    os.makedirs(execution_dir, exist_ok=True)

    file = declare.Language[language[0]]
    code = file.file.format(id=submission_id)
    executable = file.executable.format(id=submission_id)

    command = declare.Compiler[compiler[0]]
    image = command.image.format(version=compiler[1])
    compile = command.compile.format(
        source=code, executable=executable, version=language[1]
    )
    execute = command.execute.format(executable=executable)

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
            warn = DockerClient.containers.run(
                image=image,
                command=compile,
                detach=False,
                stdout=True,
                stderr=True,
                volumes=[f"{execution_dir}:/compile"],
                working_dir="/compile",
                mem_limit=COMPILER_MEM_LIMIT,
            )

        if warn:
            yield "compiler", declare.Status.COMPILE_WARN.value, {"warn": warn}
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

    def yield_and_save(data: typing.Any):
        data = utils.padding(data, 3, {})
        results.append(data)
        yield data[0], data[1], data[2]

    for i in range(test_range[0], test_range[1] + 1, 1):
        if abort.is_set():
            raise ABORTED()

        time: float = -1
        memory: int = -1
        output = ""
        expect = utils.read(os.path.join(testcases_dir, str(i), test_file[1]))

        command = execute
        if test_type == "std":
            command = f"cat {test_file[0]} | {execute}"

        if HARD_LIMIT:
            command = f'/bin/bash -c "ulimit -v {mem_parse(limit.memory)} && {{timeout}} {command}"'

        _execution_dir = execution_dir
        _testcases_dir = testcases_dir
        if INSIDE_DOCKER:
            JUDGYSE_DIR = os.getenv("JUDGYSE_DIR", "/judgyse")
            _execution_dir = os.path.join(JUDGYSE_DIR, *execution_dir.split("/")[2:])
            _testcases_dir = os.path.join(JUDGYSE_DIR, *testcases_dir.split("/")[2:])

        if RUN_IN_DOCKER:
            command = f'/usr/bin/time --format="--judgyse_static:amemory=%K,pmemory=%M,return=%x" {command.format(timeout="")}'

        else:
            command = f'{TIME_PATH} --format="--judgyse_static:time=%e,amemory=%K,pmemory=%M,return=%x" ' \
                      f'{command.format(timeout=TIMEOUT_PATH)}'

        try:
            if RUN_IN_DOCKER:
                container: docker.models.containers.Container = DockerClient.containers.run(
                    image=image,
                    command=command,
                    detach=True,
                    mem_limit=limit.memory,
                    network_disabled=True,
                    working_dir="/execution",
                    volumes=[
                        f"{_execution_dir}:/execution",
                        f"{TIME_PATH}:/usr/bin/time",
                        f"{_testcases_dir}/{i}/{test_file[0]}:/execution/{test_file[0]}",
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

            else:
                callback = subprocess.run(
                    command.split(),
                    cwd=_execution_dir,
                    capture_output=True,
                    timeout=limit.time,
                    check=True,
                )
                _output = callback.stdout.decode()
                statics = callback.stderr.decode().split('--judgyse_static:')[-1][:-2]

                statics = wrap(
                    [tuple(static.split("=")) for static in statics.split(",")]
                )
                time = float(statics["time"])
                memory = (int(statics["amemory"]) / 1024, int(statics["pmemory"]) / 1024)
                return_code = int(statics["return"])

            if return_code != 0:
                raise RUNTIME_ERROR("return code is not 0")

            if test_type == "file":
                output = utils.read(os.path.join(execution_dir, test_file[1]))

            else:
                output = _output

        except MEMORYLIMIT_EXCEEDED:
            yield from yield_and_save((i, declare.Status.MEMORY_LIMIT_EXCEEDED.value))
            continue

        except TIMELIMIT_EXCEEDED:
            yield from yield_and_save((i, declare.Status.TIME_LIMIT_EXCEEDED.value))
            continue

        except requests.exceptions.ConnectionError as e:
            if urllib3.exceptions.ReadTimeoutError in e.args:
                yield from yield_and_save((i, declare.Status.TIME_LIMIT_EXCEEDED.value, str(e)))
                continue

            else:
                raise SYSTEM_ERROR(*e.args) from e
        except subprocess.TimeoutExpired:
            yield from yield_and_save((i, declare.Status.TIME_LIMIT_EXCEEDED.value))
            continue

        except (
                docker.errors.ContainerError,
                docker.errors.APIError,
                subprocess.CalledProcessError,
                RUNTIME_ERROR,
        ) as error:
            # raise error
            raise SYSTEM_ERROR(*error.args) from error

        except Exception as e:
            # raise e
            raise UNKNOWN_ERROR(*e.args) from e

        comp = compare(output, expect, judge_mode)
        yield from yield_and_save(
            (
                i,
                declare.Status.ACCEPTED.value if comp else declare.Status.WRONG_ANSWER.value,
                {"time": time, "memory": memory},
            )
        )

    results.sort(reverse=True, key=lambda x: x[1])
    judge_status = results[0]

    yield "overall", judge_status[1], {}


def compare(a: str, b: str, mode: declare.JudgeMode) -> bool:
    if mode.mode == 0:
        if mode.trim_endl:
            a = "\n".join([a for a in a.split("\n") if a])
            b = "\n".join([b for b in b.split("\n") if b])
        if mode.case:
            a = a.lower()
            b = b.lower()
        return a == b
    else:
        pass
