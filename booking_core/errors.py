"""Domain errors. Each transport maps these to its own status codes."""


class BookingError(Exception):
    code = "booking_error"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or []


class ValidationError(BookingError):
    code = "validation_error"


class RoomOccupied(BookingError):
    code = "room_occupied"


class BookingNotFound(BookingError):
    code = "booking_not_found"


class IdempotencyKeyReused(BookingError):
    code = "idempotency_key_reused"


class InjectedFault(BookingError):
    code = "injected_fault"
