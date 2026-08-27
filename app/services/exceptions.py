# app/services/exceptions.py
class JobNotFound(Exception):
    pass

class InvalidTransition(Exception):
    def __init__(self, from_status: str, to_status: str):
        super().__init__(f"Cannot transition {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status