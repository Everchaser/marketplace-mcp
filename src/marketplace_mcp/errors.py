"""Safe machine-readable failures; never include raw pages or credentials."""


class MarketplaceError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
