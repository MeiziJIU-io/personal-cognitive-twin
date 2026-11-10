"""Win32 metadata; bounded UI Automation in an isolated process."""
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys

if sys.platform != 'win32':
    raise RuntimeError('实时采集仅支持 Windows；测试可在其他平台运行。')
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]

def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ((kernel32.GetTickCount() - info.dwTime) & 0xffffffff) / 1000

def locked():
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return True
    try:
        return not bool(user32.SwitchDesktop(wintypes.HANDLE(desktop)))
    finally:
        user32.CloseDesktop(desktop)

def foreground():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, title, len(title))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)
    name = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(name))
    try:
        if process:
            kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(size))
    finally:
        if process:
            kernel32.CloseHandle(process)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return {'hwnd': hwnd, 'pid': pid.value, 'app': name.value,
            'title': title.value, 'bounds': [rect.left, rect.top, rect.right, rect.bottom]}

def read_ui(hwnd):
    try:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'collector.windows', str(hwnd)],
                                capture_output=True, timeout=2.5, encoding='utf-8')
        if result.returncode:
            return {'text': '', 'complete': False, 'error': 'uia_failed'}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return {'text': '', 'complete': False, 'error': 'uia_timeout_or_invalid'}

def extract(hwnd):
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(0.5)
    root = auto.ControlFromHandle(hwnd)
    lines, seen = [], set()
    stack = [(root, 0)]
    count = 0
    protected = False
    truncated = False
    while stack and count < 250:
        control, depth = stack.pop()
        count += 1
        if control.IsOffscreen:
            continue
        if control.IsPassword:
            protected = True
            continue
        name = control.Name or ''
        if name and name not in seen:
            seen.add(name)
            lines.append(name[:1000])
        if depth < 8:
            stack.extend((child, depth + 1) for child in reversed(control.GetChildren()))
        else:
            truncated = True
    text = '\n'.join(lines)
    # UIA names cannot prove visual completeness. Periodic visual audit remains necessary.
    print(json.dumps({'text': text[:16000], 'complete': False,
                      'protected': protected, 'truncated': bool(stack) or truncated or len(text) > 16000}, ensure_ascii=True))

if __name__ == '__main__':
    extract(int(sys.argv[1]))
