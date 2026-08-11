"""Secret-free container health check."""

from .boundary import ProductionExecutionBoundary

if __name__ == "__main__":
    if ProductionExecutionBoundary.health()["production_state"] != "DISARMED":
        raise SystemExit(1)
