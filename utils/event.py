class Event:
    _flag: bool = False

    def __init__(self, flag: bool = False):
        self._flag = flag
    
    def set(self):
        self._flag = True

    def clear(self):
        self._flag = False

    def is_set(self):
        return self._flag