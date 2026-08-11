"""M15 signer service placeholder: healthy, isolated, and permanently disarmed."""

import time

from .boundary import ProductionExecutionBoundary


def main() -> None:
    boundary = ProductionExecutionBoundary()
    boundary.preflight()


if __name__ == "__main__":
    try:
        main()
    except PermissionError:
        while True:
            time.sleep(3600)
