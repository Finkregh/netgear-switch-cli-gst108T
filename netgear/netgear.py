"""Command line interface for Netgear GS108T switches.

The GS108T shipped with two completely different web interfaces, so the
backend is picked automatically:

* **v1** (firmware 3.0.x) -- ``/login.cgi``, ``/vlanConfiguration.html``
* **v2** (firmware 5.x)  -- ``/base/main_login.html``, ``/switching/dot1q/*``

Use ``--firmware v1`` / ``--firmware v2`` to skip detection.
"""

import os
import sys
from optparse import OptionParser

import requests

from .base import SwitchError
from .v1 import Gs108tV1
from .v2 import Gs108tV2

__author__ = "Philip Peshin"
__copyright__ = "Copyright 2014, Philip Peshin"
__license__ = "GPL"
__version__ = "0.0.1"
__email__ = "phil.peshin@gmail.com"

BACKENDS = {"v1": Gs108tV1, "v2": Gs108tV2}

COMMANDS = {
    ("vlan", "list"): "vlan_list",
    ("vlan", "add"): "vlan_add",
    ("vlan", "del"): "vlan_delete",
    ("vlan", "delete"): "vlan_delete",
    ("vlan", "membership"): "vlan_membership",
    ("vlan", "pvid"): "vlan_pvid",
}

USAGE = """
Netgear GS108T management utility
---------------------------------

List all VLANs:
    netgear vlan list

Add VLAN(s):
    netgear vlan add <comma separated VLAN ids> [<VLAN name>]

Delete VLAN(s):
    netgear vlan del <comma separated VLAN ids>

Print VLAN membership for all VLANs:
    netgear vlan membership

Print VLAN membership for VLANs:
    netgear vlan membership <comma separated VLAN ids>

Set VLAN membership for VLANs:
    netgear vlan membership <comma separated VLAN ids> <input>
Membership input format (one character per port):
    . - removed
    U - untagged
    T - tagged
Example:
    netgear vlan membership 100,101 U....TTT
Untag port #1; remove ports 2,3,4,5; Tag ports 6,7,8 on VLANs 100 and 101

List PVID:
    netgear vlan pvid

Set PVID
    netgear vlan pvid <comma separated ports, starting with 1> <VLAN> [mode vlan | all]
Examples:
    netgear vlan pvid 4,6,8 30 all
    netgear vlan pvid 3,4,5 40 vlan

The mode argument is only meaningful on GS108T v2 hardware; the v1 firmware
has no admit mode and ignores it.
"""


def normalize_argv(argv):
    """Accept ``-p=secret`` as an alias for ``-p secret``.

    ``optparse`` would otherwise read the value as ``=secret``.
    """
    result = []
    for arg in argv:
        if arg.startswith("-p=") and not arg.startswith("--"):
            result.extend(["-p", arg[3:]])
        else:
            result.append(arg)
    return result


def detect_firmware(host, timeout, verbose=False):
    """Probe the switch and return ``"v1"`` or ``"v2"``.

    Unknown paths make this firmware family drop the connection instead of
    returning 404, so a failed probe shows up as a connection error.
    """
    session = requests.Session()
    session.headers["Connection"] = "close"
    probes = (
        ("v2", "/base/main_login.html", lambda text: True),
        ("v1", "/login.html", lambda text: "login.cgi" in text),
    )
    for firmware, path, matches in probes:
        try:
            response = session.get(f"http://{host}{path}", timeout=timeout)
        except requests.RequestException:
            continue
        if response.status_code == 200 and matches(response.text):
            if verbose:
                print(f"detected firmware {firmware} ({path})", file=sys.stderr)
            return firmware
    raise SwitchError(
        f"could not detect the firmware on {host}: neither /base/main_login.html "
        f"(v2) nor /login.html (v1) responded. Check the host and try "
        f"--firmware v1|v2",
    )


def connect(options):
    if options.firmware == "auto":
        name = detect_firmware(options.host, options.timeout, options.verbose)
    else:
        name = options.firmware
    return BACKENDS[name](options.host, options.password, options.timeout)


def main():
    parser = OptionParser(usage=USAGE)
    parser.add_option(
        "",
        "--host",
        default=os.getenv("NETGEAR_HOST"),
        help="Netgear hostname or IP (or NETGEAR_HOST)",
    )
    parser.add_option(
        "-p",
        "--password",
        default=os.getenv("NETGEAR_PASSWORD", "password"),
        help="Netgear password (or NETGEAR_PASSWORD)",
    )
    parser.add_option(
        "",
        "--firmware",
        default="auto",
        choices=["auto", "v1", "v2"],
        help="switch firmware family; default auto-detect",
    )
    parser.add_option(
        "",
        "--timeout",
        type="float",
        default=15,
        help="per-request timeout in seconds (default 15)",
    )
    parser.add_option(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="report the detected firmware and retries",
    )
    options, args = parser.parse_args(normalize_argv(sys.argv[1:]))

    if not options.host:
        parser.error(
            "use --host or the NETGEAR_HOST environment variable to specify "
            "the switch address",
        )
    if len(args) < 2:
        parser.print_help()
        sys.exit(1)

    command = COMMANDS.get((args[0], args[1]))
    if command is None:
        parser.error(f"unknown command: {' '.join(args[:2])}")

    switch = None
    try:
        switch = connect(options)
        switch.login()
        getattr(switch, command)(*args[2:])
    except SwitchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if switch is not None:
            switch.persist_session()


if __name__ == "__main__":
    main()
