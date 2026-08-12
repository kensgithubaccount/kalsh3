from pathlib import Path

COMPOSE = Path("docker-compose.yml").read_text()


def test_live_redis_hardening_runs_as_redis_without_restoring_capabilities() -> None:
    redis = COMPOSE.split("\n  redis:\n", 1)[1].split("\n  nats:\n", 1)[0]
    assert 'user: "redis"' in redis
    assert "cap_drop: [ALL]" in redis
    assert "no-new-privileges:true" in redis
    assert "cap_add" not in redis


def test_caddy_receives_hostname_and_only_proxy_ports_are_published() -> None:
    caddy = COMPOSE.split("  caddy:", 1)[1].split("  web:", 1)[0]
    assert "KPV3_HOSTNAME:" in caddy
    assert 'ports: ["80:80", "443:443"]' in caddy
    assert COMPOSE.count("ports:") == 1


def test_nats_healthcheck_is_monitoring_only_and_never_signals_lame_duck() -> None:
    nats = COMPOSE.split("\n  nats:\n", 1)[1].split("\nsecrets:", 1)[0]
    assert '"-m", "8222"' in nats
    assert "http://127.0.0.1:8222/healthz" in nats
    assert "--signal" not in nats
    assert "ldm" not in nats
    assert "ports:" not in nats


def test_host_prerequisite_is_persistent_and_fail_checked() -> None:
    script = Path("deploy/ubuntu/prepare-host.sh").read_text()
    assert "/etc/sysctl.d/60-kalshi-v3-redis.conf" in script
    assert "vm.overcommit_memory = 1" in script
    assert "sysctl -n vm.overcommit_memory" in script
