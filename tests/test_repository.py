import pytest

from booking_core.errors import (
    BookingNotFound,
    IdempotencyKeyReused,
    RoomOccupied,
    ValidationError,
)
from booking_core.repository import BookingRepository


def test_creates_booking(repository):
    booking, replayed = repository.create_booking(" Room-1 ", 60)
    assert not replayed
    assert booking["room"] == "Room-1"
    assert repository.get_booking(booking["id"]) == booking
    assert repository.count_active() == 1


@pytest.mark.parametrize(
    ("room", "duration", "field"),
    [("", 60, "room"), ("   ", 60, "room"), ("x" * 31, 60, "room"),
     ("Room", 14, "duration_minutes"), ("Room", 181, "duration_minutes")],
)
def test_rejects_invalid_input(repository, room, duration, field):
    with pytest.raises(ValidationError) as error:
        repository.create_booking(room, duration)
    assert [detail["field"] for detail in error.value.details] == [field]


def test_room_is_unique_case_insensitively(repository):
    repository.create_booking("Room-1", 60)
    with pytest.raises(RoomOccupied):
        repository.create_booking("room-1", 30)


def test_room_can_be_booked_again_after_cancel(repository):
    booking, _ = repository.create_booking("Room-1", 60)
    repository.cancel_booking(booking["id"])
    repository.create_booking("Room-1", 60)
    with pytest.raises(BookingNotFound):
        repository.cancel_booking(booking["id"])


def test_idempotent_repeat_returns_original(repository):
    first, _ = repository.create_booking("Room-1", 60, "key-1")
    again, replayed = repository.create_booking("Room-1", 60, "key-1")
    assert replayed
    assert again == first
    assert repository.count_active() == 1


def test_idempotency_key_with_different_body_is_rejected(repository):
    repository.create_booking("Room-1", 60, "key-1")
    with pytest.raises(IdempotencyKeyReused):
        repository.create_booking("Room-2", 60, "key-1")


def test_two_repositories_share_one_source_of_truth(tmp_path):
    """Models the HTTP and gRPC processes opening the same database file."""
    path = str(tmp_path / "shared.db")
    http_side, grpc_side = BookingRepository(path), BookingRepository(path)
    booking, _ = http_side.create_booking("Room-1", 60)
    with pytest.raises(RoomOccupied):
        grpc_side.create_booking("ROOM-1", 60)
    assert grpc_side.get_booking(booking["id"]) == booking


def test_fail_next_is_used_up(repository):
    repository.set_fault("create-booking", 0, 1, 2, False)
    assert repository.take_fail_next("create-booking")
    assert repository.take_fail_next("create-booking")
    assert not repository.take_fail_next("create-booking")
