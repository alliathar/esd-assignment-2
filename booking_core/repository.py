"""SQLite storage shared by the HTTP API and gRPC containers.

Both containers mount the same Docker volume and open the same database file.
Each write runs in a BEGIN IMMEDIATE transaction, which takes SQLite's write
lock before reading, so the "is this room free?" check and the insert cannot
interleave with another process. The UNIQUE room_key column is a second
guarantee that a room never has two active bookings.
"""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from booking_core import rules
from booking_core.errors import BookingNotFound, IdempotencyKeyReused, RoomOccupied

DEFAULT_DB_PATH = "/data/bookings.db"
IDEMPOTENCY_TTL = timedelta(hours=24)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    room TEXT NOT NULL,
    room_key TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    booking_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faults (
    operation TEXT PRIMARY KEY,
    delay_ms INTEGER NOT NULL DEFAULT 0,
    every_nth INTEGER NOT NULL DEFAULT 1,
    fail_next INTEGER NOT NULL DEFAULT 0,
    fail_always INTEGER NOT NULL DEFAULT 0
);
"""


def _now():
    return datetime.now(timezone.utc)


def _row_to_booking(row):
    return {
        "id": row["id"],
        "room": row["room"],
        "duration_minutes": row["duration_minutes"],
        "created_at": row["created_at"],
    }


class BookingRepository:
    def __init__(self, path=None):
        self.path = path or os.getenv("BOOKING_DB_PATH", DEFAULT_DB_PATH)
        self._schema_ready = False
        self._schema_lock = Lock()

    def _connect(self):
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    Path(self.path).parent.mkdir(parents=True, exist_ok=True)
                    connection = sqlite3.connect(self.path, timeout=5)
                    try:
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.executescript(SCHEMA)
                    finally:
                        connection.close()
                    self._schema_ready = True

        # isolation_level=None: transactions are started explicitly below.
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _read(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()

    # Bookings

    def create_booking(self, room, duration_minutes, idempotency_key=None):
        """Create a booking; return (booking, replayed).

        With an idempotency key, a repeat of the same request returns the
        booking stored the first time instead of creating another one.
        """
        clean_room, duration = rules.validate_booking(room, duration_minutes)
        key = rules.validate_idempotency_key(idempotency_key)
        request_hash = hashlib.sha256(
            json.dumps(
                {"room": clean_room, "duration_minutes": duration},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        with self._write() as connection:
            if key:
                cutoff = (_now() - IDEMPOTENCY_TTL).isoformat()
                connection.execute(
                    "DELETE FROM idempotency_keys WHERE created_at < ?",
                    (cutoff,),
                )
                stored = connection.execute(
                    "SELECT request_hash, booking_json FROM idempotency_keys "
                    "WHERE key = ?",
                    (key,),
                ).fetchone()
                if stored:
                    if stored["request_hash"] != request_hash:
                        raise IdempotencyKeyReused(
                            "Idempotency key was already used with a "
                            "different request body"
                        )
                    return json.loads(stored["booking_json"]), True

            booking = {
                "id": str(uuid.uuid4()),
                "room": clean_room,
                "duration_minutes": duration,
                "created_at": _now().isoformat(),
            }
            try:
                connection.execute(
                    "INSERT INTO bookings "
                    "(id, room, room_key, duration_minutes, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        booking["id"],
                        booking["room"],
                        rules.room_key(clean_room),
                        booking["duration_minutes"],
                        booking["created_at"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RoomOccupied(
                    "Room already has an active booking"
                ) from error

            if key:
                connection.execute(
                    "INSERT INTO idempotency_keys "
                    "(key, request_hash, booking_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (key, request_hash, json.dumps(booking), booking["created_at"]),
                )

        return booking, False

    def get_booking(self, booking_id):
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
        if row is None:
            raise BookingNotFound("Booking not found")
        return _row_to_booking(row)

    def list_bookings(self):
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM bookings ORDER BY created_at"
            ).fetchall()
        return [_row_to_booking(row) for row in rows]

    def cancel_booking(self, booking_id):
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
            if row is None:
                raise BookingNotFound("Booking not found")
            connection.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        return _row_to_booking(row)

    def count_active(self):
        with self._read() as connection:
            return connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

    # Fault configuration, stored here so both transports see the same fault.

    def get_fault(self, operation):
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM faults WHERE operation = ?", (operation,)
            ).fetchone()
        if row is None:
            return {"delay_ms": 0, "every_nth": 1, "fail_next": 0, "fail_always": False}
        return {
            "delay_ms": row["delay_ms"],
            "every_nth": row["every_nth"],
            "fail_next": row["fail_next"],
            "fail_always": bool(row["fail_always"]),
        }

    def set_fault(self, operation, delay_ms, every_nth, fail_next, fail_always):
        with self._write() as connection:
            connection.execute(
                "INSERT INTO faults "
                "(operation, delay_ms, every_nth, fail_next, fail_always) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(operation) DO UPDATE SET "
                "delay_ms = excluded.delay_ms, every_nth = excluded.every_nth, "
                "fail_next = excluded.fail_next, fail_always = excluded.fail_always",
                (operation, delay_ms, every_nth, fail_next, int(fail_always)),
            )
        return self.get_fault(operation)

    def take_fail_next(self, operation):
        """Atomically use up one of the 'fail the next N calls' allowance."""
        with self._write() as connection:
            cursor = connection.execute(
                "UPDATE faults SET fail_next = fail_next - 1 "
                "WHERE operation = ? AND fail_next > 0",
                (operation,),
            )
            return cursor.rowcount == 1
