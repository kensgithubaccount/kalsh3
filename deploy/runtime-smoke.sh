#!/bin/sh
set -eu

cleanup() {
    docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export KPV3_HOSTNAME=${KPV3_HOSTNAME:-smoke.invalid}

docker compose up -d redis nats

wait_healthy() {
    service=$1
    attempts=30
    while [ "$attempts" -gt 0 ]; do
        container=$(docker compose ps -q "$service")
        status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
        [ "$status" = healthy ] && return 0
        [ "$status" = exited ] && docker compose logs "$service" && return 1
        attempts=$((attempts - 1))
        sleep 2
    done
    docker compose logs "$service"
    return 1
}

wait_healthy redis
wait_healthy nats

# Exercise the exact NATS healthcheck repeatedly and prove it neither restarts nor
# makes the client port unavailable.
nats_id=$(docker compose ps -q nats)
before=$(docker inspect --format '{{.RestartCount}}' "$nats_id")
i=0
while [ "$i" -lt 10 ]; do
    docker inspect --format '{{json .Config.Healthcheck.Test}}' "$nats_id" | grep -q '/healthz'
    docker exec "$nats_id" wget -q -O /dev/null http://127.0.0.1:8222/healthz
    i=$((i + 1))
done
response=$(printf 'CONNECT {"verbose":false}\r\nPING\r\n' | docker exec -i "$nats_id" sh -c 'nc -w 2 127.0.0.1 4222')
printf '%s' "$response" | grep -q PONG
after=$(docker inspect --format '{{.RestartCount}}' "$nats_id")
[ "$before" = "$after" ]
[ "$(docker inspect --format '{{.State.Running}}' "$nats_id")" = true ]
