"""Record the Rust agent's setup wizard and pairing window with ffmpeg gdigrab.

The agent runs sandboxed: APPDATA and LOCALAPPDATA point into C:/tmp/inquisitor-agent-demo,
nothing is installed, and no input device is allowlisted, so it captures nothing.
Usage: python agent_capture.py wizard | pair
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Release build: gh release download --pattern "*windows-x64.exe*" -D demo_video/stack/run/agent-exe
EXE = HERE.parent / "stack" / "run" / "agent-exe" / "Inquisitor-0.7.2-windows-x64.exe"
TAKES = HERE.parent / "takes"
SANDBOX = Path(r"C:/tmp/inquisitor-agent-demo")
SERVER = "http://127.0.0.1:5000"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.GetDlgItem.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
WM_COMMAND, WM_CHAR, EM_LINESCROLL, BM_CLICK = 0x0111, 0x0102, 0x00B6, 0x00F5
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def env() -> dict[str, str]:
    (SANDBOX / "Roaming").mkdir(parents=True, exist_ok=True)
    (SANDBOX / "Local").mkdir(parents=True, exist_ok=True)
    return {**os.environ, "APPDATA": str(SANDBOX / "Roaming"), "LOCALAPPDATA": str(SANDBOX / "Local"),
            "COMPUTERNAME": "DEMO-PC"}


def wait_window(title: str | None = None, cls: str | None = None, timeout: float = 15) -> int:
    end = time.time() + timeout
    while time.time() < end:
        if hwnd := user32.FindWindowW(cls, title):
            return hwnd
        time.sleep(0.1)
    raise TimeoutError(f"window {title or cls} did not appear")


def children(hwnd: int) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []

    def visit(child: int, _: int) -> bool:
        text, cls = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(child, text, 512)
        user32.GetClassNameW(child, cls, 64)
        if user32.IsWindowVisible(child):
            found.append((child, cls.value, text.value))
        return True

    user32.EnumChildWindows(hwnd, ENUM(visit), 0)
    return found


def button(hwnd: int, label: str) -> int:
    for child, cls, text in children(hwnd):
        if cls.lower() == "button" and text.replace("&", "") == label:
            return child
    raise LookupError(f"no visible button {label!r}: {[t for _, c, t in children(hwnd) if c.lower() == 'button']}")


def click(hwnd: int, label: str) -> None:
    user32.PostMessageW(button(hwnd, label), BM_CLICK, 0, 0)


class Recording:
    def __init__(self, title: str, name: str):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "gdigrab", "-framerate", "30", "-draw_mouse", "0",
             "-i", f"title={title}", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
             "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", str(TAKES / f"{name}.mp4")],
            stdin=subprocess.PIPE,
        )
        time.sleep(0.8)

    def stop(self) -> None:
        self.proc.communicate(b"q")


def wizard() -> None:
    proc = subprocess.Popen([str(EXE), "setup"], env=env())
    hwnd = wait_window("Inquisitor setup")
    time.sleep(1)
    rec = Recording("Inquisitor setup", "agent_01_terms")
    time.sleep(2.5)
    edit = next(child for child, cls, _ in children(hwnd) if cls.lower() == "edit")
    for _ in range(30):
        user32.SendMessageW(edit, EM_LINESCROLL, 0, 1)
        time.sleep(0.1)
    time.sleep(2)
    rec.stop()
    click(hwnd, "Accept")
    time.sleep(1.2)
    rec = Recording("Inquisitor setup", "agent_02_location")
    time.sleep(3)
    rec.stop()
    click(hwnd, "Next")
    time.sleep(1.2)
    rec = Recording("Inquisitor setup", "agent_03_ready")
    time.sleep(3.5)
    rec.stop()
    click(hwnd, "Cancel")
    proc.wait(10)


def pair() -> None:
    config = SANDBOX / "Roaming" / "inquisitor" / "config.ini"
    text = config.read_text(encoding="utf-8")
    if "server_url" not in text:
        config.write_text(text.rstrip("\n") + f"\nserver_url={SERVER}\n", encoding="utf-8")
    code_file = TAKES / "pairing-code.txt"
    tray = subprocess.Popen([str(EXE), "tray"], env=env())
    tray_hwnd = wait_window(cls="InquisitorTrayWnd")
    print("tray up; waiting for pairing code", flush=True)
    while not code_file.exists():
        time.sleep(0.2)
    code = code_file.read_text().strip()
    user32.PostMessageW(tray_hwnd, WM_COMMAND, 30, 0)
    title = "Inquisitor: Connect to your account"
    hwnd = wait_window(title)
    time.sleep(0.8)
    rec = Recording(title, "agent_04_pair")
    time.sleep(1.5)
    edit = user32.GetDlgItem(hwnd, 11)
    for char in code:
        user32.SendMessageW(edit, WM_CHAR, ord(char), 0)
        time.sleep(0.18)
    time.sleep(0.8)
    user32.PostMessageW(user32.GetDlgItem(hwnd, 1), BM_CLICK, 0, 0)
    time.sleep(4)
    rec.stop()
    time.sleep(1)
    user32.PostMessageW(tray_hwnd, WM_COMMAND, 50, 0)
    try:
        tray.wait(10)
    except subprocess.TimeoutExpired:
        tray.kill()
    time.sleep(1)
    (TAKES / "agent-paired.txt").write_text("1")


def cleanup() -> None:
    """Undo the HKCU entries and processes a stray keypress could create by installing into the sandbox."""
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Uninstall\Inquisitor") as key:
            if str(SANDBOX) in winreg.QueryValueEx(key, "InstallLocation")[0]:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + r"\Uninstall\Inquisitor")
                print("removed sandbox Uninstall entry")
    except FileNotFoundError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Run", 0, winreg.KEY_ALL_ACCESS) as key:
            if str(SANDBOX) in winreg.QueryValueEx(key, "Inquisitor")[0]:
                winreg.DeleteValue(key, "Inquisitor")
                print("removed sandbox Run entry")
    except FileNotFoundError:
        pass
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.Path -like '*inquisitor-agent-demo*' } | Stop-Process -Force"], check=False)


if __name__ == "__main__":
    try:
        {"wizard": wizard, "pair": pair}[sys.argv[1]]()
    finally:
        cleanup()
