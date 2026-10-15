"""Persist the switch web session between runs.

The GS108T web UI allows only a handful of simultaneous logins, has no logout
endpoint and keeps idle sessions around for minutes.  Logging in on every
invocation therefore fills the table and locks the switch out, so the session
cookie is cached in the user's state directory and reused while it still
works.
"""

import json
import os

APP_DIR = "netgear-gs108t"


def state_dir():
    """Return the per-user state directory (XDG_STATE_HOME or ~/.local/state)."""
    base = os.environ.get("XDG_STATE_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, APP_DIR)


def session_path(host):
    safe = "".join(char if char.isalnum() or char in "-._" else "_" for char in host)
    return os.path.join(state_dir(), f"session-{safe}.json")


def load(host):
    """Return the cached cookies for ``host`` as a list of dicts."""
    try:
        with open(session_path(host), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    cookies = data.get("cookies")
    if not isinstance(cookies, list):
        return []
    return [
        cookie
        for cookie in cookies
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie
    ]


def save(host, cookies):
    """Cache ``cookies`` for ``host``.  Best effort; failures are ignored."""
    if not cookies:
        return
    path = session_path(host)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # The cookie is a bearer token, so keep the file private.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"host": host, "cookies": cookies}, handle)
    except OSError:
        pass


def clear(host):
    """Forget the cached session for ``host``."""
    try:
        os.remove(session_path(host))
    except OSError:
        pass
