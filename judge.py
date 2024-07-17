import docker.models
import docker.models.containers
from declare import JudgeMode, Limit, JudgeResult, Status, Compiler, File
import pydantic
import typing
import os
import docker
import fastapi
import utils
import docker.errors
import requests
import urllib3
import threading
import queue

__all__ = ["judge_dir", "execution_dir", "testcases_dir", "DockerClient"]

judge_dir = os.path.abspath("judge")
execution_dir = os.path.join(judge_dir, "execution")
testcases_dir = os.path.join(judge_dir, "testcases")
COMPILER_MEM_LIMIT = os.getenv("COMPILER_MEM_LIMIT", "1024m")
DockerClient = None

try:
    DockerClient = docker.from_env()
except docker.errors.DockerException as error:
    if error.__str__().startswith("Error while fetching server API version"):
        raise Exception("Cannot connect to Docker daemon, is it running?")
    else:
        raise error


def judge(
    submission_id: str,
    language: typing.Tuple[str, typing.Optional[int]],
    compiler: typing.Tuple[str, typing.Union[typing.Literal["latest"], str]],
    test_range: typing.Tuple[int, int],
    test_file: typing.Tuple[str, str],
    test_type: typing.Literal["file", "std"],
    jugde_mode: JudgeMode,
    limit: Limit,
    ws: fastapi.WebSocket,
    abort: threading.Event,
    msg: queue.Queue
):
    def send_json(content: typing.Any):
        content = (
            content.model_dump() if isinstance(content, pydantic.BaseModel) else content
        )
        print(content)
        msg.put(["result", content])

    file = File[language[0]]
    code = file.file.format(id=submission_id)
    executable = file.executable.format(id=submission_id)

    command = Compiler[compiler[0]]
    image = command.image.format(version=compiler[1])
    compile = command.compile.format(
        source=code, executable=executable, version=language[1]
    )
    execute = command.execute.format(executable=executable)
    print(image, compile, execute)

    """
    Compile
    """
    try:
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
            send_json(
                JudgeResult(id=-1, status=Status.COMPILE_WARN.value, warn=warn)
            )
    except docker.errors.ContainerError as e:
        return send_json(
            JudgeResult(id=-1, status=Status.COMPILE_ERROR.value, error=str(e))
        )
    except docker.errors.APIError as e:
        return send_json(
            JudgeResult(id=-1, status=Status.SYSTEM_ERROR.value, error=str(e))
        )
    except Exception as e:
        return send_json(
            JudgeResult(id=-1, status=Status.SYSTEM_ERROR.value, error=str(e))
        )

    """
    Execute
    """

    results: typing.List[JudgeResult] = []

    def send_and_save(data: typing.Any) -> None:
        results.append(data)
        return send_json(data)

    for i in range(test_range[0], test_range[1] + 1, 1):
        if abort.is_set():
            return send_json(JudgeResult(id=-1, status=Status.ABORTED.value))

        output = ""
        expect = utils.read(os.path.join(testcases_dir, str(i), test_file[1]))
        time = -1

        try:
            container: docker.models.containers.Container = DockerClient.containers.run(
                image=image,
                command=(
                    execute
                    if test_type == "file"
                    else f"cat {test_file[0]} | {execute}"
                ),
                detach=True,
                volumes=[
                    f"{execution_dir}:/execution",
                    f"{testcases_dir}/{i}/{test_file[0]}:/execution/{test_file[0]}",
                ],
                working_dir="/execution",
                mem_limit=limit.memory,
                network_disabled=True,
            )
            container.wait(timeout=limit.time)
            inspect = DockerClient.api.inspect_container(container.id)
            if str(inspect["State"]["OOMKilled"]).lower() == "true":
                send_and_save(
                    JudgeResult(id=i, status=Status.MEMORY_LIMIT_EXCEEDED.value)
                )
            else:
                output = None
                if test_type == 'file':
                    output = utils.read(os.path.join(execution_dir, test_file[1]))
                else:
                    output =  container.logs().decode("utf-8")

                time = utils.str_to_timestamp(
                    inspect["State"]["FinishedAt"]
                ) - utils.str_to_timestamp(inspect["State"]["StartedAt"])
                container.remove()
        except docker.errors.ContainerError as e:
            send_and_save(
                JudgeResult(id=i, status=Status.COMPILE_ERROR.value, error=str(e))
            )
            continue
        except docker.errors.APIError as e:
            send_and_save(
                JudgeResult(id=i, status=Status.SYSTEM_ERROR.value, error=str(e))
            )
            continue
        except requests.exceptions.ConnectionError as e:
            if urllib3.exceptions.ReadTimeoutError in e.args:
                send_and_save(
                    JudgeResult(id=i, status=Status.TIME_LIMIT_EXCEEDED.value)
                )
                continue
            else:
                send_and_save(
                    JudgeResult(id=i, status=Status.SYSTEM_ERROR.value, error=str(e))
                )
                continue
        except Exception as e:
            # raise e
            send_and_save(
                JudgeResult(id=i, status=Status.SYSTEM_ERROR.value, error=str(e))
            )
            continue

        comp = compare(output, expect, jugde_mode)
        send_and_save(
            JudgeResult(
                id=i,
                status=Status.ACCEPTED.value if comp else Status.WRONG_ANSWER.value,
                time=time,
            )
        )

    results.sort(reverse=True, key=lambda x: x.status)
    judge_status = results[0]

    return send_json(
        JudgeResult(
            id=-1,
            status=judge_status.status,
        )
    )


def compare(a: str, b: str, mode: JudgeMode) -> bool:
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
