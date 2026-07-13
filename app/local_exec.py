"""Resource-limited launcher for reviewed local experiment implementations."""

from __future__ import annotations

import os
import runpy
import sys


def _set_limits(cpu_seconds: int, memory_bytes: int, file_bytes: int) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows has no resource module.
        return

    limits = (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_AS, memory_bytes),
        (resource.RLIMIT_FSIZE, file_bytes),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    )
    if hasattr(resource, "RLIMIT_NPROC"):
        limits += ((resource.RLIMIT_NPROC, 64),)
    for resource_id, requested in limits:
        try:
            _, hard = resource.getrlimit(resource_id)
            ceiling = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(resource_id, (ceiling, ceiling))
        except (OSError, ValueError):
            # Some macOS/container limits are unsupported. The parent timeout,
            # process group, and bounded output files remain mandatory backstops.
            continue


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    target = os.path.realpath(sys.argv[1])
    if not os.path.isfile(target):
        return 2
    cpu_seconds = max(1, min(600, int(sys.argv[2])))
    memory_bytes = max(256 * 1024 * 1024, int(sys.argv[3]))
    file_bytes = max(64 * 1024, int(sys.argv[4]))
    _set_limits(cpu_seconds, memory_bytes, file_bytes)
    sys.argv = [target]
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
