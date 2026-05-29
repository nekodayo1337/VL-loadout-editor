"""Loadout Editor — edit your VALORANT loadout without launching the game."""

import os
import sys
import traceback

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src.logs import Logging
from src.requestsV import Requests

version = "alpha"

HAS_CONSOLE = sys.stdout is not None

if sys.platform == "win32" and HAS_CONSOLE:
    try:
        os.system("")
    except Exception:
        pass
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        os.system("title Loadout Editor")
    except Exception:
        pass


def notify(text):
    if HAS_CONSOLE:
        print(text)
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "Loadout Editor", 0x40)
        except Exception:
            pass


class _NoLaunch:
    def start_valorant(self):
        pass

    def LockfileError(self, path, ignoreLockfile=False):
        return os.path.exists(path) and not ignoreLockfile


def make_requests(log):
    lockfile = os.path.join(
        os.getenv("LOCALAPPDATA"), R"Riot Games\Riot Client\Config\lockfile"
    )
    if not os.path.exists(lockfile):
        notify(
            "Riot Client is not running. Please open the Riot Client and log in "
            "(you do NOT need to launch VALORANT itself), then run this again."
        )
        return None
    return Requests(version, log, _NoLaunch())


def main():
    log = Logging().log
    try:
        req = make_requests(log)
        if req is None:
            if HAS_CONSOLE:
                input("press enter to exit...\n")
            return
        from src.loadout_gui import LoadoutGui
        LoadoutGui(log, req, version).serve()
    except KeyboardInterrupt:
        pass
    except Exception:
        log(traceback.format_exc())
        notify("The Loadout Editor encountered an error. See the logs folder.")
        if HAS_CONSOLE:
            input("press enter to exit...\n")


if __name__ == "__main__":
    main()
