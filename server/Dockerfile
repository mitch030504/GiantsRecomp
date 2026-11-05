# Giants netplay relay. Single stdlib-only Python process, non-root, no
# capabilities. The container root filesystem is read-only (see compose).
FROM python:3.13-alpine

# Fixed uid/gid so host-side permissions are predictable.
RUN addgroup -g 10001 giants \
    && adduser -D -u 10001 -G giants giants

WORKDIR /app
COPY giants_relay.py /app/giants_relay.py
RUN chown -R giants:giants /app

USER 10001:10001

# Python must not try to write bytecode or buffer stdout on a read-only root.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIANTS_CONTROL_PORT=32200 \
    GIANTS_RELAY_MIN=41100 \
    GIANTS_RELAY_MAX=41199 \
    GIANTS_TTL=30 \
    GIANTS_MAX_SESSIONS=100 \
    GIANTS_RATE_PER_SECOND=50 \
    GIANTS_RATE_BURST=100 \
    GIANTS_LOG_LEVEL=INFO

# Control socket + one UDP socket per room.
EXPOSE 32200/udp
EXPOSE 41100-41199/udp

# Probe 127.0.0.1:$GIANTS_CONTROL_PORT (32200 by default) from inside the
# container: HEALTH (kind 7) must return a matching HEALTH_ACK (kind 8).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "/app/giants_relay.py", "--healthcheck"]

CMD ["python", "-u", "/app/giants_relay.py"]
