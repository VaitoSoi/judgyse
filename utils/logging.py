import logging
import copy
import http
import click
import sys
import typing


class ColorizedFormatter(logging.Formatter):
    level_name_colors = {
        logging.DEBUG: lambda level_name: click.style(str(level_name), fg="cyan"),
        logging.INFO: lambda level_name: click.style(str(level_name), fg="bright_blue"),
        logging.WARNING: lambda level_name: click.style(str(level_name), fg="bright_yellow"),
        logging.ERROR: lambda level_name: click.style(str(level_name), fg="bright_red"),
        logging.CRITICAL: lambda level_name: click.style(str(level_name), fg="red"),
    }

    def __init__(
            self,
            fmt: str | None = None,
            datefmt: str | None = None,
            style: typing.Literal["%", "{", "$"] = "%",
            use_colors: bool | None = None,
    ):
        if use_colors in (True, False):
            self.use_colors = use_colors
        else:
            self.use_colors = sys.stdout.isatty()
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def color_level_name(self, level_name: str, level_no: int) -> str:
        def default(level_name: str) -> str:
            return str(level_name)  # pragma: no cover

        func = self.level_name_colors.get(level_no, default)
        return func(level_name)

    def formatMessage(self, record):
        recordcopy = copy.copy(record)
        levelname = f"{recordcopy.levelname:<7}"
        if self.use_colors:
            levelname = self.color_level_name(levelname, recordcopy.levelno)
        recordcopy.__dict__["levelname"] = levelname
        return super().formatMessage(recordcopy)


class AccessFormatter(ColorizedFormatter):
    @staticmethod
    def phrase_color(status_code: int, message: str) -> str:
        if http.HTTPStatus(status_code).is_redirection:
            return click.style(str(message), fg="bright_white")
        if http.HTTPStatus(status_code).is_success:
            return click.style(str(message), fg="green")
        if http.HTTPStatus(status_code).is_informational:
            return click.style(str(message), fg="yellow")
        if http.HTTPStatus(status_code).is_client_error:
            return click.style(str(message), fg="red")
        if http.HTTPStatus(status_code).is_server_error:
            return click.style(str(message), fg="bright_red")

    def formatMessage(self, record):
        recordcopy = copy.copy(record)
        (
            client_addr,
            method,
            full_path,
            http_version,
            status_code,
        ) = recordcopy.args
        status_code_phrase = None
        try:
            status_code_phrase = http.HTTPStatus(status_code).phrase
        except ValueError:
            status_code_phrase = ""
        request_line = f"{method} {full_path} HTTP/{http_version}"
        recordcopy.__dict__["message"] = \
            f'{client_addr} - "{request_line}" {self.phrase_color(status_code, f"{status_code} {status_code_phrase}")}'

        return super().formatMessage(recordcopy)


def formatter(name: str, formatter_: logging.Formatter = ColorizedFormatter):
    return formatter_( # noqa
        f"%(asctime)s :: {name:<{15}} :: %(levelname)-7s :: %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )


def console_handler(name: str, formatter_: logging.Formatter = ColorizedFormatter):
    handler = logging.StreamHandler()
    handler.setFormatter(formatter(name, formatter_))
    return handler
