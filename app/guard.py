"""
多开实例数限制：通过命名互斥锁控制最大同时运行数。
从 license.lic 读取 max_instances，默认 10。
"""
import ctypes
import ctypes.wintypes

from . import license


def check_and_acquire():
    """尝试获取运行时互斥锁。
    返回 (ok, slot) -- ok=False 表示已达到最大实例数，slot 是分配的槽位号（1-based）。"""
    max_instances = license.get_max_instances()
    for slot in range(1, max_instances + 1):
        name = f"EasyFlow_Instance_{slot}"
        try:
            h = ctypes.windll.kernel32.CreateMutexW(None, False, name)
            if h and ctypes.windll.kernel32.GetLastError() != 183:
                return True, slot
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            continue
    return False, 0
