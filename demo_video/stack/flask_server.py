"""Demo-only: run Frontend/server.py with the auth forward pointed at port 13001."""
import os
import runpy
import sys

import requests

original = requests.request


def remapped(method, url, *args, **kwargs):
    return original(method, url.replace("127.0.0.1:3001/", "127.0.0.1:13001/"), *args, **kwargs)


requests.request = remapped
sys.path.insert(0, os.getcwd())
runpy.run_path(os.path.abspath("server.py"), run_name="__main__")
