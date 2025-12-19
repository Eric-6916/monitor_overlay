from dataclasses import dataclass
from typing import Dict, Optional
import psutil
import time


@dataclass
class RateState:
    last_ts: float
    last_disk_read: int
    last_disk_write: int
    last_net_sent: int
    last_net_recv: int


class PsutilReader:
    def __init__(self):
        d = psutil.disk_io_counters()
        n = psutil.net_io_counters()
        self.state = RateState(
            last_ts=time.time(),
            last_disk_read=d.read_bytes if d else 0,
            last_disk_write=d.write_bytes if d else 0,
            last_net_sent=n.bytes_sent if n else 0,
            last_net_recv=n.bytes_recv if n else 0,
        )

    def read(self) -> Dict[str, Optional[float]]:
        now = time.time()
        dt = max(1e-3, now - self.state.last_ts)

        d = psutil.disk_io_counters()
        n = psutil.net_io_counters()

        disk_read_bps = None
        disk_write_bps = None
        up_bps = None
        down_bps = None

        if d:
            disk_read_bps = (d.read_bytes - self.state.last_disk_read) / dt
            disk_write_bps = (d.write_bytes - self.state.last_disk_write) / dt
            self.state.last_disk_read = d.read_bytes
            self.state.last_disk_write = d.write_bytes

        if n:
            up_bps = (n.bytes_sent - self.state.last_net_sent) / dt
            down_bps = (n.bytes_recv - self.state.last_net_recv) / dt
            self.state.last_net_sent = n.bytes_sent
            self.state.last_net_recv = n.bytes_recv

        self.state.last_ts = now

        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_freq = psutil.cpu_freq()
        vm = psutil.virtual_memory()

        return {
            "cpu_usage": float(cpu_percent) if cpu_percent is not None else None,
            "cpu_freq_psutil": float(cpu_freq.current) if cpu_freq and cpu_freq.current else None,

            "ram_usage": float(vm.percent) if vm else None,
            "ram_used_gb": float(vm.used) / (1024**3) if vm else None,
            "ram_avail_gb": float(vm.available) / (1024**3) if vm else None,

            "disk_read_mb_s": float(disk_read_bps) / (1024**2) if disk_read_bps is not None else None,
            "disk_write_mb_s": float(disk_write_bps) / (1024**2) if disk_write_bps is not None else None,

            "net_up_mb_s": float(up_bps) / (1024**2) if up_bps is not None else None,
            "net_down_mb_s": float(down_bps) / (1024**2) if down_bps is not None else None,
        }
