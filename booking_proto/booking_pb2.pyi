from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CreateBookingRequest(_message.Message):
    __slots__ = ("room", "duration_minutes", "idempotency_key")
    ROOM_FIELD_NUMBER: _ClassVar[int]
    DURATION_MINUTES_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    room: str
    duration_minutes: int
    idempotency_key: str
    def __init__(self, room: _Optional[str] = ..., duration_minutes: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class Booking(_message.Message):
    __slots__ = ("id", "room", "duration_minutes", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    ROOM_FIELD_NUMBER: _ClassVar[int]
    DURATION_MINUTES_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    room: str
    duration_minutes: int
    created_at: str
    def __init__(self, id: _Optional[str] = ..., room: _Optional[str] = ..., duration_minutes: _Optional[int] = ..., created_at: _Optional[str] = ...) -> None: ...

class CreateBookingResponse(_message.Message):
    __slots__ = ("booking", "replayed")
    BOOKING_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    booking: Booking
    replayed: bool
    def __init__(self, booking: _Optional[_Union[Booking, _Mapping]] = ..., replayed: _Optional[bool] = ...) -> None: ...
