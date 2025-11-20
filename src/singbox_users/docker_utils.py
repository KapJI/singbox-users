"""Docker helpers used by the singbox-users TUI."""

from __future__ import annotations

from pathlib import Path
import subprocess


def check_config(config_path: Path, image: str) -> tuple[bool, str]:
    """Validate sing-box config.json using `docker run ... check`."""

    config_path = Path(config_path)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{config_path}:/etc/sing-box/config.json:ro",
        image,
        "check",
        "-c",
        "/etc/sing-box/config.json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=25,
            check=False,
        )
        ok = proc.returncode == 0
        return ok, (proc.stdout or "").strip()
    except FileNotFoundError:
        return False, "docker not found; skipped check"
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as exc:
        return False, f"check error: {exc}"


def restart_container(container: str) -> tuple[bool, str]:
    """Restart a docker container by name."""

    try:
        proc = subprocess.run(
            ["docker", "restart", container],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=25,
            check=False,
        )
        return proc.returncode == 0, (proc.stdout or "").strip()
    except FileNotFoundError:
        return False, "docker not found"
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as exc:
        return False, f"restart error: {exc}"
