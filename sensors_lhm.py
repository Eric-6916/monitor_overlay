import os
from typing import Dict, Optional

import clr  # pythonnet

DLL_PATH = os.path.join(os.path.dirname(__file__), "LibreHardwareMonitorLib", "LibreHardwareMonitorLib.dll")
clr.AddReference(DLL_PATH)

from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType  # type: ignore


def _sensor_value(sensor) -> Optional[float]:
    try:
        v = sensor.Value
        return float(v) if v is not None else None
    except Exception:
        return None


class LhmReader:
    """
    使用 LibreHardwareMonitor 读取硬件传感器：
    - CPU: 温度、频率、功耗、使用率(部分机器可能有 Load 传感器)
    - GPU: 温度、频率、功耗、显存使用(部分机型)、使用率
    - 硬盘: 温度、使用率(有的能读到)
    """
    def __init__(self):
        self.comp = Computer()
        self.comp.IsCpuEnabled = True
        self.comp.IsGpuEnabled = True
        self.comp.IsMemoryEnabled = True
        self.comp.IsStorageEnabled = True
        self.comp.IsNetworkEnabled = False  # 网络速度用 psutil 更可靠
        self.comp.Open()

    def close(self):
        try:
            self.comp.Close()
        except Exception:
            pass

    def read(self) -> Dict[str, Optional[float]]:
        """
        返回扁平字典，单位：
        - 温度: °C
        - 频率: MHz
        - 功耗: W
        - 使用率: %
        - 显存: MB（若能取到）
        """
        data: Dict[str, Optional[float]] = {
            "cpu_temp": None,
            "cpu_power": None,
            "cpu_freq": None,
            "cpu_load": None,

            "gpu_temp": None,
            "gpu_power": None,
            "gpu_freq": None,
            "gpu_load": None,
            "gpu_vram_used": None,   # MB (如可用)
            "gpu_vram_total": None,  # MB (如可用)

            "disk_temp": None,
            "disk_load": None,
        }

        for hw in self.comp.Hardware:
            hw.Update()

            # 有些硬件有子硬件（比如 CPU Package/核心、GPU 多个节点）
            sub_hws = list(hw.SubHardware) if getattr(hw, "SubHardware", None) else []
            for shw in sub_hws:
                shw.Update()

            def scan_one(h):
                # CPU
                if h.HardwareType == HardwareType.Cpu:
                    for s in h.Sensors:
                        if s.SensorType == SensorType.Temperature and (s.Name or "").lower().find("package") >= 0:
                            data["cpu_temp"] = _sensor_value(s) or data["cpu_temp"]
                        elif s.SensorType == SensorType.Power and (s.Name or "").lower().find("package") >= 0:
                            data["cpu_power"] = _sensor_value(s) or data["cpu_power"]
                        elif s.SensorType == SensorType.Clock and (s.Name or "").lower().find("core") >= 0:
                            # 取一个代表性的 core clock（也可以改成平均）
                            v = _sensor_value(s)
                            if v is not None:
                                data["cpu_freq"] = max(data["cpu_freq"] or 0.0, v)
                        elif s.SensorType == SensorType.Load and (s.Name or "").lower().find("total") >= 0:
                            data["cpu_load"] = _sensor_value(s) or data["cpu_load"]

                # GPU
                if h.HardwareType in (HardwareType.GpuNvidia, HardwareType.GpuAmd, HardwareType.GpuIntel):
                    for s in h.Sensors:
                        name = (s.Name or "").lower()
                        if s.SensorType == SensorType.Temperature and ("gpu" in name or "core" in name):
                            data["gpu_temp"] = _sensor_value(s) or data["gpu_temp"]
                        elif s.SensorType == SensorType.Power and ("gpu" in name or "package" in name or "total" in name):
                            data["gpu_power"] = _sensor_value(s) or data["gpu_power"]
                        elif s.SensorType == SensorType.Clock and ("core" in name):
                            v = _sensor_value(s)
                            if v is not None:
                                data["gpu_freq"] = max(data["gpu_freq"] or 0.0, v)
                        elif s.SensorType == SensorType.Load and ("core" in name or "gpu" in name or "total" in name):
                            data["gpu_load"] = _sensor_value(s) or data["gpu_load"]
                        elif s.SensorType == SensorType.SmallData:
                            # LHM 有时会把显存用量放 SmallData（取决于实现/驱动）
                            if "memory used" in name or "vram used" in name:
                                data["gpu_vram_used"] = _sensor_value(s) or data["gpu_vram_used"]
                            if "memory total" in name or "vram total" in name:
                                data["gpu_vram_total"] = _sensor_value(s) or data["gpu_vram_total"]

                # Storage
                if h.HardwareType == HardwareType.Storage:
                    for s in h.Sensors:
                        name = (s.Name or "").lower()
                        if s.SensorType == SensorType.Temperature and ("temperature" in name or "drive" in name):
                            data["disk_temp"] = _sensor_value(s) or data["disk_temp"]
                        elif s.SensorType == SensorType.Load and ("used space" in name or "activity" in name or "total" in name):
                            data["disk_load"] = _sensor_value(s) or data["disk_load"]

            scan_one(hw)
            for shw in sub_hws:
                scan_one(shw)

        return data
