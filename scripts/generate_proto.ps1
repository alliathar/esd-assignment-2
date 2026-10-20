# Generate the Python message classes and gRPC stub/servicer from the contract.
# Run from the project root. Output: booking_proto/booking_pb2*.py(i)
python -m grpc_tools.protoc -I . `
    --python_out=. --grpc_python_out=. --pyi_out=. `
    booking_proto/booking.proto
