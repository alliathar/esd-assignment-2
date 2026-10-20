"""Controlled fault injection for the create-booking operation.

The configuration lives in the shared database, so one admin call affects the
HTTP and gRPC versions of the operation alike. Each process counts its own
calls for the every-nth delay, which keeps Assignment 1's "every fifth
request" behaviour per service.
"""

import time
from itertools import count
from threading import Lock

from booking_core.errors import InjectedFault

CREATE_BOOKING = "create-booking"


class FaultInjector:
    def __init__(self, repository, operation=CREATE_BOOKING):
        self.repository = repository
        self.operation = operation
        self._calls = count(1)
        self._lock = Lock()

    def apply(self):
        """Sleep and/or raise InjectedFault according to the stored config."""
        with self._lock:
            call_number = next(self._calls)

        fault = self.repository.get_fault(self.operation)

        if fault["delay_ms"] and call_number % fault["every_nth"] == 0:
            time.sleep(fault["delay_ms"] / 1000)

        if fault["fail_always"]:
            raise InjectedFault("Injected failure (fail_always)")
        if fault["fail_next"] and self.repository.take_fail_next(self.operation):
            raise InjectedFault("Injected failure (fail_next)")
