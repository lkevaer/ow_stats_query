"""Small per-operation circuit breaker; player privacy errors never open it."""
import time


class UpstreamCircuit:
    def __init__(self, threshold=5, cooldown=30):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.open_until = {}, {}

    def remaining(self, operation):
        return max(0, self.open_until.get(operation, 0) - time.monotonic())

    def success(self, operation):
        self.failures.pop(operation, None)
        self.open_until.pop(operation, None)

    def failure(self, operation, *, immediate=False):
        self.failures[operation] = self.failures.get(operation, 0) + 1
        if immediate or self.failures[operation] >= self.threshold:
            self.open_until[operation] = time.monotonic() + self.cooldown

    def status(self):
        return {key: round(self.remaining(key), 1) for key in self.open_until if self.remaining(key) > 0}
