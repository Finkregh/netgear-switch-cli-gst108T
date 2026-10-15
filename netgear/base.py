"""Shared plumbing for the switch backends.

The Netgear GS108T family has two very different web UIs:

* ``GS108T v1`` (firmware 3.0.x): ``/login.cgi`` + ``/vlanConfiguration.html``
* ``GS108T v2`` (firmware 5.x): ``/base/main_login.html`` + ``/switching/dot1q/*``

Both are wrapped by the small interface defined here; :mod:`netgear.netgear`
picks the right one at runtime.
"""

import requests


class SwitchError(Exception):
    """Any recoverable error while talking to the switch."""


class SwitchBase:
    """Common session handling and the command interface.

    Subclasses implement :meth:`login` plus the ``vlan_*`` commands. The
    signatures are the ones the CLI dispatches to, so the argument order is
    part of the user facing interface.
    """

    #: human readable firmware description, shown in ``--help``/errors
    firmware = "unknown"

    def __init__(self, host, password, timeout=15):
        self.host = host
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self._logged_in = False

    def url(self, path):
        return f"http://{self.host}{path}"

    # -- interface --------------------------------------------------------

    def login(self):
        raise NotImplementedError

    def vlan_list(self):
        raise NotImplementedError

    def vlan_add(self, ids, name=None):
        raise NotImplementedError

    def vlan_delete(self, ids):
        raise NotImplementedError

    def vlan_membership(self, ids=None, membership=None):
        raise NotImplementedError

    def vlan_pvid(self, ports=None, vlan=None, type="all"):
        raise NotImplementedError

    def persist_session(self):
        """Hook called after a successful command to save session state."""

    # -- helpers ----------------------------------------------------------

    def get(self, path, **kwargs):
        try:
            return self.session.get(self.url(path), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise SwitchError(f"GET {path} failed: {exc}")

    def post(self, path, data, **kwargs):
        try:
            return self.session.post(
                self.url(path),
                data=data,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise SwitchError(f"POST {path} failed: {exc}")

    @staticmethod
    def parse_ids(ids):
        """Split a ``1,2,3`` argument, validating that every item is a VLAN id."""
        result = []
        for item in str(ids).split(","):
            item = item.strip()
            if not item:
                continue
            if not item.isdigit() or not 1 <= int(item) <= 4094:
                raise SwitchError(f"invalid VLAN id: {item!r}")
            result.append(item)
        if not result:
            raise SwitchError("no VLAN ids given")
        return result

    @staticmethod
    def parse_ports(ports, count):
        """Split a ``1,2,3`` port argument, validating the range."""
        result = []
        for item in str(ports).split(","):
            item = item.strip()
            if not item:
                continue
            if not item.isdigit() or not 1 <= int(item) <= count:
                raise SwitchError(f"invalid port number: {item!r} (1-{count})")
            result.append(item)
        if not result:
            raise SwitchError("no ports given")
        return result
