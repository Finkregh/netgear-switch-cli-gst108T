"""GS108T v1 backend (firmware 3.0.x).

This firmware serves a single page per section and embeds the whole
configuration in it as JavaScript arrays::

    Vlan_Vid[i]    = "7";
    Vlan_Name[i]   = "Dsl";
    Vlan_Member[i] = "22222222";
    PVid[i]        = "1";

Changes are applied by posting to the same ``.cgi`` handlers the page's own
JavaScript uses.  The switch allows only a handful of concurrent web sessions,
has no logout endpoint and keeps idle ones alive for minutes, so the session
cookie is cached (see :mod:`netgear.state`) and reused while it still works.
The embedded server also stalls on keep-alive, so every request asks for a
fresh connection.
"""

import re
import sys
import time

from requests.cookies import create_cookie

from . import state
from .base import SwitchBase, SwitchError

LOGIN_PAGE = "/login.html"
LOGIN_CGI = "/login.cgi"
VLAN_PAGE = "/vlanConfiguration.html"
VLAN_CGI = "/vlanconfig.cgi"
MEMBERSHIP_CGI = "/membership.cgi"
PVID_CGI = "/setpvid.cgi"

# Membership is one digit per port, in port order.
REMOVED = "0"
UNTAGGED = "1"
TAGGED = "2"
# The CLI keeps the historical ". / U / T" notation of the v2 code.
TO_SWITCH = {".": REMOVED, "U": UNTAGGED, "T": TAGGED}
FROM_SWITCH = {REMOVED: ".", UNTAGGED: "U", TAGGED: "T"}
# The switch stores "no name" as this sentinel.
NO_NAME = "NULLDESCRIPTION"

# If even the cached session is gone the switch may still have every login
# slot taken by other clients.  Poll until one frees up before giving up.
SESSION_WAIT = 60
SESSION_POLL_INTERVAL = 3


class Gs108tV1(SwitchBase):
    """Netgear GS108T v1 (firmware 3.0.x, "Broadcom WebSuperSmart" web UI)."""

    firmware = "GS108T v1 (firmware 3.0.x)"

    def __init__(self, host, password, timeout=15):
        super().__init__(host, password, timeout)
        # The embedded server stalls on keep-alive; force one request per
        # connection.
        self.session.headers["Connection"] = "close"

    # -- session ----------------------------------------------------------

    def login(self):
        """Reuse the cached session if it still works, else log in.

        Logging in creates a new session that the switch keeps around for
        minutes, so reusing one keeps us from filling up its small session
        table.  ``rtime`` is required by ``login.cgi`` but its value is not
        checked; the login page fills it in from the browser clock.
        """
        cached = state.load(self.host)
        if cached:
            self._restore_cookies(cached)
            if self._is_logged_in():
                self._logged_in = True
                return
            state.clear(self.host)
        self._login_fresh()
        self._logged_in = True
        self.persist_session()

    def persist_session(self):
        """Store the cookies the switch handed out last.

        The switch rotates the session cookie on every response, so they have
        to be saved after the command has run, not just after logging in;
        otherwise the next run sends a stale value and the session is dropped.
        """
        if self._logged_in:
            state.save(self.host, self._session_cookies())

    def _session_cookies(self):
        """Return the session cookies with the scope the switch gave them."""
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain or self.host,
                "path": cookie.path or "/",
            }
            for cookie in self.session.cookies
        ]

    def _restore_cookies(self, cookies):
        """Install cached cookies with their original domain and path.

        The scope matters: ``WSSHID`` belongs to ``/login.cgi``, and sending it
        to any other page makes the switch treat the request as
        unauthenticated.  Restoring without a domain would also leave a second
        copy of the session cookie next to the one the switch sets on every
        response, and both would then be sent.
        """
        self.session.cookies.clear()
        for cookie in cookies:
            self.session.cookies.set_cookie(
                create_cookie(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain") or self.host,
                    path=cookie.get("path") or "/",
                ),
            )

    def _is_logged_in(self):
        """Check whether the current cookies still authenticate."""
        try:
            response = self.get(VLAN_PAGE)
        except SwitchError:
            return False
        return response.status_code == 200 and "Vlan_Vid" in response.text

    def _login_fresh(self):
        self.session.cookies.clear()
        self.get(LOGIN_PAGE)
        deadline = time.monotonic() + SESSION_WAIT
        warned = False
        while True:
            response = self.post(
                LOGIN_CGI,
                {"password": self.password, "rtime": int(time.time() * 1000 / 500)},
                allow_redirects=False,
            )
            if "loginwrong" in response.text:
                raise SwitchError("login failed: wrong password")
            if "Maximal number of logined user" not in response.text:
                if response.status_code in (
                    301,
                    302,
                    303,
                    307,
                ) or "Broadcom-WebSuperSmart" in response.headers.get("Set-Cookie", ""):
                    return
                raise SwitchError(
                    f"unexpected response to {LOGIN_CGI} (HTTP {response.status_code})",
                )
            if time.monotonic() >= deadline:
                raise SwitchError(
                    f"login failed: the switch already has its maximum number of "
                    f"web sessions (waited {SESSION_WAIT}s); close other web UI "
                    f"sessions and retry",
                )
            if not warned:
                print(
                    "switch has no free web session; waiting for one to expire",
                    file=sys.stderr,
                )
                warned = True
            time.sleep(SESSION_POLL_INTERVAL)

    def _get_page(self, path):
        response = self.get(path)
        if response.status_code != 200:
            raise SwitchError(f"{path} returned HTTP {response.status_code}")
        if (
            "loginwrong" in response.text
            or "Maximal number of logined user" in response.text
        ):
            raise SwitchError(f"{path} served the login page: not logged in")
        return response.text

    def _submit(self, path, data):
        response = self.post(path, data, allow_redirects=False)
        if response.status_code not in (200, 302):
            raise SwitchError(f"{path} returned HTTP {response.status_code}")
        return response

    # -- reading ----------------------------------------------------------

    def vlan_cfg(self):
        """Return ``(vlans, pvids)`` for the current configuration.

        ``vlans`` maps a VLAN id to ``{"name": str, "member": str}`` where
        ``member`` has one digit per port, and ``pvids`` is the per-port PVID
        list in port order.
        """
        text = self._get_page(VLAN_PAGE)
        # \b keeps these from also matching the QVlan_* arrays used by the
        # port-based VLAN pages.
        vids = re.findall(r'\bVlan_Vid\[i\]\s*=\s*"(\d+)";', text)
        names = re.findall(r'\bVlan_Name\[i\]\s*=\s*"([^"]*)";', text)
        members = re.findall(r'\bVlan_Member\[i\]\s*=\s*"(\d+)";', text)
        pvids = re.findall(r'\bPVid\[i\]\s*=\s*"(\d+)";', text)
        if not vids or len(vids) != len(names) or len(vids) != len(members):
            raise SwitchError(f"could not parse the VLAN table from {VLAN_PAGE}")
        vlans = {}
        for vid, name, member in zip(vids, names, members):
            vlans[vid] = {
                "name": "" if name == NO_NAME else name,
                "member": member,
            }
        return vlans, pvids

    def vlan_list(self):
        vlans, _ = self.vlan_cfg()
        for vid in sorted(vlans, key=int):
            print(f"{vid.ljust(4)} {vlans[vid]['name']}")

    def vlan_membership(self, ids=None, membership=None):
        vlans, pvids = self.vlan_cfg()
        if ids and membership is not None:
            self._set_membership(vlans, pvids, ids, membership)
            return
        selected = self._select(vlans, ids)
        for vid in selected:
            print(f"{vid.ljust(4)} {self._decode_membership(vlans[vid]['member'])}")

    def vlan_pvid(self, ports=None, vlan=None, type="all"):
        vlans, pvids = self.vlan_cfg()
        if not ports:
            for port, pvid in enumerate(pvids, start=1):
                # This firmware has no "admit all / vlan only" mode.
                print(f"{str(port).ljust(4)} {pvid.rjust(4)}")
            return
        if type not in ("all", "vlan"):
            raise SwitchError(f"invalid PVID mode: {type!r} (use 'vlan' or 'all')")
        if type == "vlan":
            print("note: this firmware has no 'vlan only' admit mode, ignoring it")
        if vlan not in vlans:
            raise SwitchError(f"no such VLAN: {vlan}")
        member = vlans[vlan]["member"]
        wanted = self.parse_ports(ports, len(pvids))
        for port in wanted:
            if member[int(port) - 1] == REMOVED:
                raise SwitchError(
                    f"port {port} is not a member of VLAN {vlan}; "
                    f"add the membership first",
                )
        self._submit(
            PVID_CGI,
            {"config": "".join(f"{port},{vlan}," for port in wanted)},
        )

    # -- writing ----------------------------------------------------------

    def vlan_add(self, ids, name=None):
        vlans, _ = self.vlan_cfg()
        added = 0
        for vid in self.parse_ids(ids):
            if vid in vlans:
                # Adding an existing id would silently overwrite its name and
                # wipe its membership, so refuse instead.
                print(f"VLAN {vid} already exists, skipping")
                continue
            vlan_name = name or "vlan-" + vid.zfill(4)
            self._submit(VLAN_CGI, {"action": "1", "config": f"{vid},{vlan_name},"})
            added += 1
        return added

    def vlan_delete(self, ids):
        vlans, pvids = self.vlan_cfg()
        wanted = self.parse_ids(ids)
        # Validate everything before changing anything.
        for vid in wanted:
            if vid not in vlans:
                raise SwitchError(f"no such VLAN: {vid}")
            if vid in pvids:
                ports = ",".join(
                    str(i) for i, p in enumerate(pvids, start=1) if p == vid
                )
                raise SwitchError(
                    f"VLAN {vid} is the PVID of port(s) {ports}; "
                    f"reassign the PVID before deleting it",
                )
        config = "".join(f"{vid},{vlans[vid]['name'] or NO_NAME}," for vid in wanted)
        self._submit(VLAN_CGI, {"action": "2", "config": config})

    def _set_membership(self, vlans, pvids, ids, value):
        wanted = self.parse_ids(ids)
        digits = self._encode_membership(value, len(pvids))
        for vid in wanted:
            if vid not in vlans:
                raise SwitchError(f"no such VLAN: {vid}")
            for port, pvid in enumerate(pvids, start=1):
                if pvid == vid and digits[port - 1] == REMOVED:
                    raise SwitchError(
                        f"port {port} cannot leave VLAN {vid}: it is the port's PVID",
                    )
        for vid in wanted:
            config = f"{vid},{vlans[vid]['name'] or NO_NAME},{digits}"
            self._submit(MEMBERSHIP_CGI, {"config": config})

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _select(vlans, ids):
        if not ids:
            return sorted(vlans, key=int)
        wanted = SwitchBase.parse_ids(ids)
        for vid in wanted:
            if vid not in vlans:
                raise SwitchError(f"no such VLAN: {vid}")
        return wanted

    @staticmethod
    def _decode_membership(member):
        return "".join(FROM_SWITCH.get(digit, "?") for digit in member)

    @staticmethod
    def _encode_membership(value, ports):
        value = str(value).strip()
        if len(value) != ports:
            raise SwitchError(
                f"membership must have {ports} characters (one per port), got {len(value)}",
            )
        digits = ""
        for char in value:
            try:
                digits += TO_SWITCH[char.upper()]
            except KeyError:
                raise SwitchError(
                    f"invalid membership character {char!r}: "
                    f"use '.' (removed), 'U' (untagged) or 'T' (tagged)",
                ) from None
        return digits
