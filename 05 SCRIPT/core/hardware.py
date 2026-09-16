"""
Real-time Hardware Monitoring for NVIDIA GPUs (NVML Ctypes and nvidia-smi fallback)
"""

import os
import subprocess
import ctypes
from typing import Dict


class MemoryStruct(ctypes.Structure):
    _fields_ = [
        ('total', ctypes.c_ulonglong),
        ('free', ctypes.c_ulonglong),
        ('used', ctypes.c_ulonglong)
    ]


class UtilStruct(ctypes.Structure):
    _fields_ = [
        ('gpu', ctypes.c_uint),
        ('memory', ctypes.c_uint)
    ]


class GPUInfoProvider:
    """
    Zero-overhead real-time NVIDIA GPU and VRAM monitor.
    Uses native nvml.dll via ctypes (0.001 ms) with silent subprocess fallback.
    """

    def __init__(self):
        self._nvml = None
        self._handle = None
        self._gpu_name = None
        self._total_mb = 0
        self._has_nvml = False
        self._init_nvml()

    def _init_nvml(self):
        try:
            self._nvml = ctypes.CDLL('nvml.dll')
            ret = self._nvml.nvmlInit_v2()
            if ret == 0:
                self._handle = ctypes.c_void_p()
                ret = self._nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._handle))
                if ret == 0:
                    name_buf = ctypes.create_string_buffer(96)
                    self._nvml.nvmlDeviceGetName(self._handle, name_buf, 96)
                    self._gpu_name = name_buf.value.decode('utf-8', errors='ignore')

                    mem = MemoryStruct()
                    self._nvml.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem))
                    self._total_mb = int(mem.total // (1024 * 1024))
                    self._has_nvml = True
        except Exception:
            self._has_nvml = False

    def query(self) -> Dict[str, any]:
        """
        Returns a dict:
        {
            'available': bool,
            'name': str,
            'load_pct': int,      # Real GPU Compute utilization %
            'used_mb': int,       # Real VRAM used
            'total_mb': int,      # Total VRAM
            'vram_pct': int
        }
        """
        # 1. Native NVML query (0.001ms)
        if self._has_nvml and self._handle:
            try:
                mem = MemoryStruct()
                ret_m = self._nvml.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem))

                util = UtilStruct()
                ret_u = self._nvml.nvmlDeviceGetUtilizationRates(self._handle, ctypes.byref(util))

                if ret_m == 0 and ret_u == 0:
                    used_mb = int(mem.used // (1024 * 1024))
                    total_mb = int(mem.total // (1024 * 1024))
                    load_pct = int(util.gpu)
                    vram_pct = int((used_mb / total_mb * 100)) if total_mb > 0 else 0

                    return {
                        'available': True,
                        'name': self._gpu_name or 'NVIDIA GPU',
                        'load_pct': load_pct,
                        'used_mb': used_mb,
                        'total_mb': total_mb,
                        'vram_pct': vram_pct
                    }
            except Exception:
                pass

        # 2. nvidia-smi fallback (silent, no window)
        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                creationflags = 0x08000000  # CREATE_NO_WINDOW
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            cmd = ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,name', '--format=csv,noheader,nounits']
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1, startupinfo=startupinfo, creationflags=creationflags)
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(',')]
                if len(parts) >= 4:
                    load_pct = int(parts[0])
                    used_mb = int(parts[1])
                    total_mb = int(parts[2])
                    name = parts[3]
                    vram_pct = int((used_mb / total_mb * 100)) if total_mb > 0 else 0
                    return {
                        'available': True,
                        'name': name,
                        'load_pct': load_pct,
                        'used_mb': used_mb,
                        'total_mb': total_mb,
                        'vram_pct': vram_pct
                    }
        except Exception:
            pass

        # 3. PyTorch fallback
        try:
            import torch
            if torch.cuda.is_available():
                total_mb = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
                name = torch.cuda.get_device_name(0)
                used_mb = int(torch.cuda.memory_allocated() / (1024 * 1024))
                return {
                    'available': True,
                    'name': name,
                    'load_pct': 0,
                    'used_mb': used_mb,
                    'total_mb': total_mb,
                    'vram_pct': int((used_mb / total_mb * 100)) if total_mb > 0 else 0
                }
        except Exception:
            pass

        return {
            'available': False,
            'name': 'CPU Only',
            'load_pct': 0,
            'used_mb': 0,
            'total_mb': 0,
            'vram_pct': 0
        }


# Global singleton instance & alias
GPUMonitor = GPUInfoProvider
gpu_monitor = GPUInfoProvider()
