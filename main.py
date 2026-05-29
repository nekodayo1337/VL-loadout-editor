"""Loadout Editor — edit your VALORANT loadout without launching the game."""

import os
import sys
import traceback

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src.logs import Logging
from src.requestsV import Requests

version = "alpha"

if sys.platform == "win32":
    os.system("")
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

os.system("title Loadout Editor")


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
        print(
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
            input("press enter to exit...\n")
            return
        from src.loadout_gui import LoadoutGui
        LoadoutGui(log, req).serve()
    except KeyboardInterrupt:
        pass
    except Exception:
        log(traceback.format_exc())
        print("The Loadout Editor encountered an error. See the logs folder.")
        input("press enter to exit...\n")


if __name__ == "__main__":
    main()
