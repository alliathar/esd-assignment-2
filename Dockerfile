FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY booking_core ./booking_core
COPY booking_proto ./booking_proto
COPY grpc_service ./grpc_service
COPY app ./app

# Regenerate the gRPC code inside the image so it always matches the
# installed grpcio/protobuf versions (same command as scripts/generate_proto.sh).
RUN python -m grpc_tools.protoc -I . \
    --python_out=. --grpc_python_out=. --pyi_out=. \
    booking_proto/booking.proto

EXPOSE 8000 50051 9102

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
