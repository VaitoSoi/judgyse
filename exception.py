class InvalidTestcaseIndex(ValueError):
    pass


class MissingField(ValueError):
    pass


class CommandNotFound(ValueError):
    pass


class NoActiveThread(ValueError):
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

