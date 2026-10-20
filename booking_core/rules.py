"""Booking rules used by both the HTTP and gRPC entry points."""

from booking_core.errors import ValidationError

ROOM_MIN_LENGTH = 1
ROOM_MAX_LENGTH = 30
DURATION_MIN_MINUTES = 15
DURATION_MAX_MINUTES = 180
IDEMPOTENCY_KEY_MAX_LENGTH = 100


def validate_booking(room, duration_minutes):
    """Return the cleaned room and duration, or raise ValidationError."""
    details = []
    clean_room = (room or "").strip()

    if not ROOM_MIN_LENGTH <= len(clean_room) <= ROOM_MAX_LENGTH:
        details.append({
            "field": "room",
            "message": (
                f"must be {ROOM_MIN_LENGTH}-{ROOM_MAX_LENGTH} characters "
                "after trimming spaces"
            ),
        })

    if not DURATION_MIN_MINUTES <= duration_minutes <= DURATION_MAX_MINUTES:
        details.append({
            "field": "duration_minutes",
            "message": (
                f"must be between {DURATION_MIN_MINUTES} and "
                f"{DURATION_MAX_MINUTES} minutes"
            ),
        })

    if details:
        raise ValidationError("Booking request is invalid", details)

    return clean_room, duration_minutes


def validate_idempotency_key(key):
    """Treat an empty key as absent; reject keys that are too long."""
    if not key:
        return None
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValidationError(
            "Idempotency key is invalid",
            [{
                "field": "idempotency_key",
                "message": f"must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters",
            }],
        )
    return key


def room_key(room):
    """Rooms are unique case-insensitively, as in Assignment 1."""
    return room.lower()
