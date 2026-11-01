"""Host inventory: everything the benchmark can observe locally, and nothing it invents.

Collection rules:

* read the kernel's own files (``/proc``, ``/sys``, ``/etc/os-release``) and the standard library;
  never call a vendor management tool, never open a socket, never shell out for hardware facts;
* an absent file is ``not_available`` with a reason, not a zero and certainly not an estimate;
* the operator may supply a facts file for a host they cannot probe (a locked-down dedicated
  server). Those values are recorded with ``source: operator_declared`` and are never mixed into
  ``source: probe`` values;
* ``host_id`` is a truncated digest of machine identity, so results from one machine can be
  compared across runs without publishing a host name. The host name itself is recorded only when
  the operator asks for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

NOT_AVAILABLE = "not_available"
AVAILABLE = "available"
OPERATOR_DECLARED = "operator_declared"

MACHINE_ID_PATHS = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
POWER_SUPPLY_GLOB = "/sys/class/power_supply/*/power_now"
POWERCAP_GLOB = "/sys/class/powercap/*/energy_uj"
THERMAL_GLOB = "/sys/class/thermal/thermal_zone*/temp"
HWMON_GLOB = "/sys/class/hwmon/hwmon*/temp*_input"
GPU_PROCFS_GLOB = "/proc/driver/*/gpus/*/information"
VRAM_SYSFS_GLOB = "/sys/class/drm/card[0-9]*/device/mem_info_vram_total"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def _glob(pattern: str) -> list[Path]:
    import glob as _glob_module

    return sorted(Path(match) for match in _glob_module.glob(pattern))


def _cpu_model() -> str | None:
    text = _read_text(Path("/proc/cpuinfo"))
    if not text:
        return None
    for line in text.splitlines():
        if line.lower().startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    return None


def _physical_cores() -> int | None:
    text = _read_text(Path("/proc/cpuinfo"))
    if not text:
        return None
    pairs = set()
    current: dict[str, str] = {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if "physical id" in current and "core id" in current:
                pairs.add((current["physical id"], current["core id"]))
            current = {}
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current[key.strip().lower()] = value.strip()
    return len(pairs) or None


def _memory_total_mb() -> int | None:
    text = _read_text(Path("/proc/meminfo"))
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            try:
                return round(int(parts[1]) / 1024)
            except (IndexError, ValueError):
                return None
    return None


def _os_release() -> dict:
    text = _read_text(Path("/etc/os-release"))
    values: dict[str, str] = {}
    if text:
        for line in text.splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
    if values.get("PRETTY_NAME"):
        return {"status": AVAILABLE, "pretty_name": values["PRETTY_NAME"],
                "id": values.get("ID"), "version_id": values.get("VERSION_ID")}
    return {"status": NOT_AVAILABLE, "reason": "/etc/os-release is not readable"}


def _gpu() -> dict:
    """Generic accelerator discovery: driver procfs entries and DRM sysfs, nothing else."""
    devices: list[dict] = []
    for information in _glob(GPU_PROCFS_GLOB):
        entry = {"source": "probe", "path": str(information), "model": None, "vram_total_mb": None}
        text = _read_text(information)
        if text:
            for line in text.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip().lower(), value.strip()
                if key == "model":
                    entry["model"] = value
                elif "total memory" in key or key == "memory":
                    entry["vram_total_mb"] = value
        devices.append(entry)
    for vram_path in _glob(VRAM_SYSFS_GLOB):
        total = _read_int(vram_path)
        if total:
            devices.append({
                "source": "probe",
                "path": str(vram_path),
                "model": None,
                "vram_total_mb": round(total / (1024 * 1024)),
            })
    if not devices:
        return {
            "status": NOT_AVAILABLE,
            "reason": "no accelerator information exposed by this host (no /proc driver entry, "
                      "no DRM memory attribute); the benchmark does not guess or query a vendor tool",
            "devices": [],
        }
    return {"status": AVAILABLE, "devices": devices}


@dataclass
class HostInfo:
    role: str
    include_hostname: bool = False
    declared: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ identity
    def host_id(self) -> str:
        material = []
        for path in MACHINE_ID_PATHS:
            text = _read_text(path)
            if text:
                material.append(text)
                break
        material.append(platform.platform())
        material.append(_cpu_model() or "")
        material.append(str(_memory_total_mb() or ""))
        digest = hashlib.sha256("|".join(material).encode("utf-8")).hexdigest()
        return f"host_{digest[:12]}"

    # ------------------------------------------------------------------ inventory
    def to_dict(self) -> dict:
        memory_mb = _memory_total_mb()
        cpu = {
            "model": _cpu_model(),
            "logical_cores": os.cpu_count(),
            "physical_cores": _physical_cores(),
            "architecture": platform.machine(),
        }
        inventory = {
            "host_id": self.host_id(),
            "hostname": self._hostname(),
            "role": self.role,
            "os": _os_release(),
            "kernel": {"status": AVAILABLE, "release": platform.release(),
                       "system": platform.system()},
            "runtime": {
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "executable": Path(sys.executable).name,
            },
            "cpu": {key: value for key, value in cpu.items()},
            "memory": {
                "status": AVAILABLE if memory_mb else NOT_AVAILABLE,
                "total_mb": memory_mb,
                "reason": None if memory_mb else "/proc/meminfo is not readable",
            },
            "gpu": _gpu(),
            "thermal": self._thermal(),
            "power": self._power_capability(),
            "storage": self._storage(),
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "declared_by_operator": self.declared or {},
        }
        return inventory

    def _hostname(self) -> dict:
        if not self.include_hostname:
            return {"status": "not_recorded",
                    "reason": "hostname is only recorded with --include-hostname"}
        return {"status": AVAILABLE, "value": platform.node()}

    def _storage(self) -> dict:
        try:
            usage = shutil.disk_usage(Path.cwd())
        except OSError as exc:  # pragma: no cover - unusual environment
            return {"status": NOT_AVAILABLE, "reason": str(exc)}
        return {
            "status": AVAILABLE,
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "device": None,
            "note": "capacity of the filesystem holding the working directory; not a speed test",
        }

    def _thermal(self) -> dict:
        zones = []
        for path in _glob(THERMAL_GLOB) + _glob(HWMON_GLOB):
            value = _read_int(path)
            if value is None:
                continue
            celsius = value / 1000.0 if value > 1000 else float(value)
            if -40.0 < celsius < 200.0:
                zones.append({"sensor": path.name, "path": str(path.parent),
                              "celsius": round(celsius, 1)})
        if not zones:
            return {"status": NOT_AVAILABLE,
                    "reason": "this host exposes no readable thermal zone", "sensors": []}
        return {"status": AVAILABLE, "sensors": zones}

    def _power_capability(self) -> dict:
        supplies = _glob(POWER_SUPPLY_GLOB)
        counters = _glob(POWERCAP_GLOB)
        if supplies:
            return {"status": AVAILABLE, "method": "power_supply_power_now",
                    "sources": [str(path) for path in supplies]}
        if counters:
            return {"status": AVAILABLE, "method": "powercap_energy_counter",
                    "sources": [str(path) for path in counters]}
        return {"status": NOT_AVAILABLE,
                "reason": "this host exposes no power meter readable by the benchmark",
                "method": None}

    # ------------------------------------------------------------------ power sampling
    def read_power_watts(self) -> float | None:
        """Instantaneous power if the host exposes it, else ``None``. No estimation."""
        total_microwatts = 0
        found = False
        for path in _glob(POWER_SUPPLY_GLOB):
            value = _read_int(path)
            if value is None:
                continue
            total_microwatts += value
            found = True
        if found:
            return round(total_microwatts / 1e6, 3)
        return None

    def read_energy_joules(self) -> tuple[float, float] | None:
        """Cumulative energy across powercap counters, with the monotonic timestamp."""
        total_microjoules = 0
        found = False
        for path in _glob(POWERCAP_GLOB):
            value = _read_int(path)
            if value is None:
                continue
            total_microjoules += value
            found = True
        if not found:
            return None
        return (time.monotonic(), total_microjoules / 1e6)

    def measure_power(self, seconds: float = 0.0, samples: int = 3) -> dict:
        """Sample power for a short window; returns a status, never a guess."""
        direct = []
        energy = []
        for index in range(max(1, samples)):
            watts = self.read_power_watts()
            if watts is not None:
                direct.append(watts)
            joules = self.read_energy_joules()
            if joules is not None:
                energy.append(joules)
            if index + 1 < samples:
                time.sleep(max(0.0, seconds) / max(1, samples))
        if direct:
            return {"status": AVAILABLE, "method": "power_supply_power_now",
                    "samples": len(direct),
                    "watts_mean": round(sum(direct) / len(direct), 3),
                    "watts_max": round(max(direct), 3)}
        if len(energy) >= 2:
            first, last = energy[0], energy[-1]
            delta_t = last[0] - first[0]
            if delta_t > 0:
                return {"status": AVAILABLE, "method": "powercap_energy_counter",
                        "samples": len(energy),
                        "watts_mean": round((last[1] - first[1]) / delta_t, 3),
                        "window_s": round(delta_t, 3)}
        return {"status": NOT_AVAILABLE,
                "reason": "no power meter readable on this host; nothing was estimated"}


def load_declared(path: Path | None) -> dict:
    """Operator-declared host facts (for a host the operator cannot probe). Validated, not trusted."""
    if path is None:
        return {}
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"gpu", "memory", "cpu", "power", "thermal", "storage", "note", "collected_with"}
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"host facts file has unknown keys: {', '.join(unknown)}")
    return document
