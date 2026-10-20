"""Business rules and storage shared by the HTTP API and the gRPC service.

Both transports import this package and call the same repository against the
same SQLite file, so there is one set of rules and one source of truth.
"""
