"""Which processes are using the microphone right now (macOS 14+, CoreAudio process objects).

No permissions needed; reads only metadata. Used by meeting_watch.py to detect Teams/Zoom calls.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import struct
from typing import List, Tuple

_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))


def _fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode("ascii"))[0]


class _Addr(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


_GLOBAL = _fourcc("glob")
_SYSTEM_OBJECT = 1
_PROCESS_LIST = _fourcc("prs#")
_PROC_PID = _fourcc("ppid")
_PROC_BUNDLE = _fourcc("pbid")
_PROC_RUNNING_INPUT = _fourcc("piri")

_ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32,
                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFRelease.argtypes = [ctypes.c_void_p]


def _get(obj: int, sel: int, buf, size: int) -> bool:
    addr = _Addr(sel, _GLOBAL, 0)
    sz = ctypes.c_uint32(size)
    return _ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(sz), buf) == 0


def _process_objects() -> List[int]:
    addr = _Addr(_PROCESS_LIST, _GLOBAL, 0)
    size = ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(_SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0:
        return []
    n = size.value // 4
    arr = (ctypes.c_uint32 * n)()
    if not _get(_SYSTEM_OBJECT, _PROCESS_LIST, arr, size.value):
        return []
    return list(arr)


def mic_users() -> List[Tuple[int, str]]:
    """[(pid, bundle_id)] of processes currently capturing audio input."""
    users = []
    for obj in _process_objects():
        running = ctypes.c_uint32(0)
        if not _get(obj, _PROC_RUNNING_INPUT, ctypes.byref(running), 4) or not running.value:
            continue
        pid = ctypes.c_int32(0)
        _get(obj, _PROC_PID, ctypes.byref(pid), 4)
        ref = ctypes.c_void_p(0)
        bundle = ""
        if _get(obj, _PROC_BUNDLE, ctypes.byref(ref), ctypes.sizeof(ctypes.c_void_p)) and ref.value:
            buf = ctypes.create_string_buffer(512)
            if _cf.CFStringGetCString(ref, buf, 512, 0x08000100):
                bundle = buf.value.decode("utf-8", "replace")
            _cf.CFRelease(ref)
        users.append((pid.value, bundle))
    return users


if __name__ == "__main__":
    for pid, bundle in mic_users():
        print(pid, bundle or "(no bundle id)")
