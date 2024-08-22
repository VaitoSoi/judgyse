class InvalidTestcaseIndex(ValueError):
    pass


class MissingField(ValueError):
    pass


class InvalidField(ValueError):
    pass


class CommandNotFound(ValueError):
    pass


class ABORTED(Exception):
    pass


class MEMORYLIMIT_EXCEEDED(Exception):
    pass


class TIMELIMIT_EXCEEDED(Exception):
    pass


class COMPILE_ERROR(Exception):
    pass


class SYSTEM_ERROR(Exception):
    pass


class RUNTIME_ERROR(Exception):
    pass


class UNKNOWN_ERROR(Exception):
    pass


class JUDGER_ERROR(Exception):
    pass
