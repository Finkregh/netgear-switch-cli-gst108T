"""GS108T v2 backend (firmware 5.x).

Kept for the original hardware this project was written against.  The paths
and form fields here are the ones the upstream tool has always used; the
switch answers unknown paths by closing the connection, so a v1 device will
simply fail every request.
"""

import re

from .base import SwitchBase

VLAN_CFG = "/switching/dot1q/vlan_cfg.html"
VLAN_PORT_CFG = "/switching/dot1q/vlan_port_cfg_rw.html"
PVID_PORT_CFG = "/switching/dot1q/qnp_port_cfg_rw.html"


class Gs108tV2(SwitchBase):
    """Netgear GS108T v2 (firmware 5.x web UI)."""

    firmware = "GS108T v2 (firmware 5.x)"

    def check_result(self, response):
        error_message = re.findall(
            r'<INPUT.*NAME="err_msg".*VALUE="\s*(.*)\s">',
            response.text,
        )
        error_flag = re.findall(
            r'<INPUT.*NAME="err_flag".*VALUE="(.*)">',
            response.text,
        )
        if len(error_flag) > 0 and int(error_flag[0]) != 0:
            print(error_message[0])
            raise SystemExit(error_flag[0])

    def login(self):
        self.post("/base/main_login.html", {"pwd": self.password})

    def vlan_cfg(self):
        response = self.get(VLAN_CFG)
        vlan_ids = dict(
            re.findall(
                r'<INPUT.*NAME="(\d*.\d*.\d*).vlanId".*VALUE="(\d*)">',
                response.text,
            ),
        )
        vlan_names = dict(
            re.findall(
                r'<INPUT.*NAME="(\d*.\d*.\d*).vlanName".*VALUE="(.*)".*>',
                response.text,
            ),
        )
        vlans = {}
        for position, vid in vlan_ids.items():
            vlans[vid] = {"name": vlan_names[position], "position": position}
        return vlans

    def vlan_list(self):
        cfg = self.vlan_cfg()
        for vid in sorted(cfg.keys(), key=lambda x: int(x)):
            print(f"{vid.ljust(4)} {cfg[vid]['name']}")

    def vlan_add(self, ids, name=None):
        for vid in ids.split(","):
            vlan_name = name or "vlan-" + vid.zfill(4)
            response = self.post(
                VLAN_CFG,
                {
                    "add": 16,
                    "vlan_id": vid,
                    "vlan_name": vlan_name,
                    "vlan_type": "Static",
                },
            )
            self.check_result(response)

    def vlan_delete(self, ids):
        cfg = self.vlan_cfg()
        params = {"delete": 16}
        for vid in ids.split(","):
            if vid not in cfg:
                print("No such VLAN: " + vid)
                raise SystemExit(1)
            params[cfg[vid]["position"] + ".CBox_1"] = "checkbox"
            params[cfg[vid]["position"] + ".vlanId"] = vid
        response = self.post(VLAN_CFG, params)
        self.check_result(response)

    def vlan_membership_set(self, ids, value):
        decode = {".": "3", "U": "2", "T": "1"}
        line = ""
        for char in value:
            line += decode[char]
            line += ","
        line += "3,3,3,3"
        for vid in ids.split(","):
            response = self.post(
                VLAN_PORT_CFG,
                {"submt": 16, "vlanid": vid, "hiddenMem": line},
            )
            self.check_result(response)

    def vlan_membership_get(self, vid):
        response = self.post(VLAN_PORT_CFG, {"submt": 0, "vlanid": vid})
        self.check_result(response)
        membership = dict(
            re.findall(
                r"toggleImage\(this,(\d),0,\'img_unit1\'\).*\/base\/images\/grey_(.).gif",
                response.text,
            ),
        )
        decode = {"b": ".", "u": "U", "t": "T"}
        result = ""
        for port in sorted(membership.keys(), key=lambda x: int(x)):
            result += decode[membership[port]]
        return result

    def vlan_membership(self, ids=None, membership=None):
        if ids:
            if membership is not None:
                self.vlan_membership_set(ids, membership)
            else:
                for vid in ids.split(","):
                    print(f"{vid.ljust(4)} {self.vlan_membership_get(vid)}")
            return
        cfg = self.vlan_cfg()
        for vid in sorted(cfg.keys(), key=lambda x: int(x)):
            print(f"{vid.ljust(4)} {self.vlan_membership_get(vid)}")

    def vlan_pvid(self, ports=None, vlan=None, type="all"):
        if not ports:
            response = self.get(PVID_PORT_CFG)
            pvid = re.findall(
                r">g(\d)<.*\s*.*>(\d*)<.*\s*.*>(\d*)<.*\s*.*>(.*)<",
                response.text,
            )
            decode = {"VLAN Only": "vlan", "Admit All": "all"}
            for port, _vlan_new, vlan_current, mode in pvid:
                print(f"{port} {vlan_current.rjust(4)} {decode[mode]}")
        else:
            port_list = ports.split(",")
            selected_ports = ""
            for port in port_list:
                selected_ports += f"g{port};"
            response = self.post(
                PVID_PORT_CFG,
                {
                    "submt": 16,
                    "selectedPorts": selected_ports,
                    "pvid": vlan,
                    "ftype": type,
                    "multiple_ports": len(port_list),
                },
            )
            self.check_result(response)
