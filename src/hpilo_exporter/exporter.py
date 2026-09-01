"""
Pulls data from specified iLO and presents as Prometheus metrics
"""

from __future__ import print_function
import sys

import ssl
import time
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse  # quote_plus,
from socketserver import ThreadingMixIn
import psutil  # process handling, zombies
import hpilo
from _socket import gaierror
from prometheus_client import (
    generate_latest,
    Summary,
    Gauge,
    CollectorRegistry,
    REGISTRY,
)


def print_err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def make_ilo_ssl_context():
    """SSL context that can talk to old iLO firmware (self-signed, TLS 1.0/1.2, weak ciphers)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except (AttributeError, ValueError):
        pass
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        ctx.set_ciphers(
            "ECDH+AESGCM:DH+AESGCM:ECDH+AES256:DH+AES256:ECDH+AES128:DH+AES:"
            "ECDH+HIGH:DH+HIGH:ECDH+3DES:DH+3DES:RSA+AESGCM:RSA+AES:RSA+HIGH:"
            "RSA+3DES:RC4-SHA:!aNULL:!eNULL:!MD5"
        )
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return ctx


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    max_children = 30
    timeout = 30


def translate(st):
    if st.upper() in ["OK", "GOOD, IN USE", "ON"]:
        return 0
    elif st.upper() == "DEGRADED":
        return 1
    elif st.upper() in ["ABSENT", "NOT INSTALLED"]:
        return -1
    else:
        return 2


def _as_bool_int(value):
    if value is True:
        return 1
    if value is False or value is None:
        return 0
    return 1 if str(value).strip().upper() in ("TRUE", "Y", "YES", "1", "ENABLED") else 0


def parse_ilo_timestamp(value):
    """Parse iLO event-log timestamps to a unix epoch, or None."""
    if not value:
        return None
    text = str(value).strip()
    if not text or text.upper().startswith("[NOT SET]"):
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return time.mktime(datetime.strptime(text, fmt).timetuple())
        except ValueError:
            continue
    return None


_PRIV_LABELS = {
    "admin_priv": "admin",
    "config_ilo_priv": "config_ilo",
    "remote_cons_priv": "remote_console",
    "reset_server_priv": "reset_server",
    "virtual_media_priv": "virtual_media",
    "login_priv": "login",
}

_METHOD_ALIASES = {
    "browser": "browser",
    "ssh": "ssh",
    "xml": "xml",
    "ribcl": "xml",
    "remote console": "remote_console",
    "ilo rbsu": "rbsu",
    "cli": "cli",
}

_METHOD_EVENT_RE = re.compile(
    r"^(?P<failed>Failed\s+)?"
    r"(?P<method>Browser|SSH|XML|RIBCL|Remote Console|iLO RBSU|CLI)\s+"
    r"(?P<action>login|logout)\s*[:\-?]?\s*"
    r"(?:IP\s*Address\s*:\s*)?"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)

_ILO_USER_EVENT_RE = re.compile(
    r"^iLO user\s+(?P<user>.+?)\s+logged\s+(?P<action>in|out)\b"
    r"(?:\s+from\s+(?P<ip>\S+))?",
    re.IGNORECASE,
)

_FAILED_LOGIN_RE = re.compile(
    r"^Failed login(?: attempt)?(?:\s+from\s+(?P<ip>\S+))?",
    re.IGNORECASE,
)

_IP_RE = re.compile(
    r"((?:\d{1,3}\.){3}\d{1,3}|(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4})"
)


def _extract_user_and_ip(rest):
    rest = (rest or "").strip(" .")
    if not rest:
        return "", ""
    ip = ""
    ip_match = _IP_RE.search(rest)
    if ip_match:
        ip = ip_match.group(1).rstrip(".)")
    user = ""
    if " - " in rest:
        user = rest.split(" - ", 1)[0].strip()
    elif ip_match and ip_match.start() > 0:
        user = rest[: ip_match.start()].strip(" :-")
    elif not ip_match:
        user = rest
    return user, ip


def parse_login_event(description):
    """
    Parse an iLO Event Log description into a login-related event.

    Returns dict with keys user, method, result, source_ip, or None.
    result is one of: success, failure, logout.
    """
    text = (description or "").strip().rstrip(".")
    if not text:
        return None

    match = _METHOD_EVENT_RE.match(text)
    if match:
        user, ip = _extract_user_and_ip(match.group("rest"))
        method = _METHOD_ALIASES.get(match.group("method").lower(), "unknown")
        if match.group("failed"):
            result = "failure"
        elif match.group("action").lower() == "logout":
            result = "logout"
        else:
            result = "success"
        return {
            "user": user or "unknown",
            "method": method,
            "result": result,
            "source_ip": ip,
        }

    match = _ILO_USER_EVENT_RE.match(text)
    if match:
        ip = (match.group("ip") or "").rstrip(".)")
        action = match.group("action").lower()
        return {
            "user": (match.group("user") or "unknown").strip(),
            "method": "unknown",
            "result": "success" if action == "in" else "logout",
            "source_ip": ip,
        }

    match = _FAILED_LOGIN_RE.match(text)
    if match:
        ip = (match.group("ip") or "").rstrip(".)")
        if not ip:
            ip_match = _IP_RE.search(text)
            ip = ip_match.group(1).rstrip(".)") if ip_match else ""
        return {
            "user": "unknown",
            "method": "unknown",
            "result": "failure",
            "source_ip": ip,
        }

    return None


class RequestHandler(BaseHTTPRequestHandler):
    """
    Endpoint handler
    """

    # P is all metrics prefix
    P = "hpilo_"

    def __init__(self, request, client_address, server):
        self.registry = None
        self.registry = CollectorRegistry(self)
        self.process_registry = REGISTRY
        self.gauges = {
            "vrm": Gauge(
                self.P + "vrm_status",
                "HP iLO vrm status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "drive": Gauge(
                self.P + "drive_status",
                "HP iLO drive status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "battery": Gauge(
                self.P + "battery_status",
                "HP iLO battery status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "battery_detail": Gauge(
                self.P + "battery_detail",
                "HP iLO battery status  0 = OK, 1 = DEGRADED",
                [
                    "label",
                    "present",
                    "status",
                    "model",
                    "spare",
                    "serial_number",
                    "capacity",
                    "firmware_version",
                    "product_name",
                    "server_name",
                ],
                registry=self.registry,
            ),
            "storage": Gauge(
                self.P + "storage_status",
                "HP iLO storage status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "fans": Gauge(
                self.P + "fans_status",
                "HP iLO all fans status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "bios_hardware": Gauge(
                self.P + "bios_hardware_status",
                "HP iLO bios_hardware status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "memory": Gauge(
                self.P + "memory_status",
                "HP iLO memory status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "memory_detail": Gauge(
                self.P + "memory_detail",
                "HP iLO memory detail info",
                [
                    "product_name",
                    "server_name",
                    "cpu_id",
                    "operating_frequency",
                    "operating_voltage",
                ],
                registry=self.registry,
            ),
            "power_supplies": Gauge(
                self.P + "power_supplies_status",
                "HP iLO power_supplies status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "power_supplies_readings": Gauge(
                self.P + "power_supplies_readings",
                "HP iLO power_supplies status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "processor": Gauge(
                self.P + "processor_status",
                "HP iLO processor status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "processor_detail": Gauge(
                self.P + "processor_detail",
                "HP iLO processor status",
                ["product_name", "server_name", "cpu_id", "name", "status", "speed"],
                registry=self.registry,
            ),
            "network": Gauge(
                self.P + "network_status",
                "HP iLO network status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "temperature": Gauge(
                self.P + "temperature_status",
                "HP iLO temperature status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "firmware_version": Gauge(
                self.P + "firmware_version",
                "HP iLO firmware version",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "nic_status": Gauge(
                self.P + "nic_status",
                "HP iLO NIC status",
                ["product_name", "server_name", "nic_name", "ip_address"],
                registry=self.registry,
            ),
            "storage_cache_health": Gauge(
                self.P + "storage_cache_health_status",
                "Cache Module status",
                ["product_name", "server_name", "controller"],
                registry=self.registry,
            ),
            "storage_controller_health": Gauge(
                self.P + "storage_controller_health_status",
                "Controller status",
                ["product_name", "server_name", "controller"],
                registry=self.registry,
            ),
            "storage_enclosure_health": Gauge(
                self.P + "storage_enclosure_health_status",
                "Enclosure status",
                ["product_name", "server_name", "controller", "enc"],
                registry=self.registry,
            ),
            "storage_ld_health": Gauge(
                self.P + "storage_ld_health_status",
                "LD status",
                ["product_name", "server_name", "controller", "logical_drive"],
                registry=self.registry,
            ),
            "storage_pd_health": Gauge(
                self.P + "storage_pd_health_status",
                "PD status",
                [
                    "product_name",
                    "server_name",
                    "controller",
                    "logical_drive",
                    "physical_drive",
                ],
                registry=self.registry,
            ),
            "temperature_value": Gauge(
                self.P + "temperature_value",
                "Temperature value",
                ["product_name", "server_name", "sensor"],
                registry=self.registry,
            ),
            "fan": Gauge(
                self.P + "fan_status",
                "HP iLO one fan status",
                ["product_name", "server_name", "fan"],
                registry=self.registry,
            ),
            "fan_speed": Gauge(
                self.P + "fan_speed",
                "HP iLO one fan value",
                ["product_name", "server_name", "fan"],
                registry=self.registry,
            ),
            "power_supply": Gauge(
                self.P + "power_supply_status",
                "HP iLO one power supply power",
                ["product_name", "server_name", "ps", "capacity_w"],
                registry=self.registry,
            ),
            "running": Gauge(
                self.P + "running_status",
                "HP iLO running status",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "oa_info": Gauge(
                self.P + "onboard_administrator_info",
                "HP iLO OnBoard Administrator Info",
                ["product_name", "server_name", "oa_ip", "encl", "location_bay"],
                registry=self.registry,
            ),
            "user_info": Gauge(
                self.P + "user_info",
                "Local iLO user account (1 = present)",
                ["product_name", "server_name", "user_login", "user_name"],
                registry=self.registry,
            ),
            "user_count": Gauge(
                self.P + "user_count",
                "Number of local iLO user accounts",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "user_privilege": Gauge(
                self.P + "user_privilege",
                "iLO user privilege flag (1 = granted, 0 = denied)",
                ["product_name", "server_name", "user_login", "privilege"],
                registry=self.registry,
            ),
            "user_scrape_success": Gauge(
                self.P + "user_scrape_success",
                "1 if local iLO user inventory was scraped, else 0",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
            "user_login_events": Gauge(
                self.P + "user_login_events",
                "Login-related events currently present in the iLO event log (IEL snapshot, not a counter)",
                ["product_name", "server_name", "user_login", "method", "result"],
                registry=self.registry,
            ),
            "user_last_login_timestamp": Gauge(
                self.P + "user_last_login_timestamp",
                "Unix timestamp of the most recent successful iLO login in the IEL",
                ["product_name", "server_name", "user_login", "method", "source_ip"],
                registry=self.registry,
            ),
            "user_last_logout_timestamp": Gauge(
                self.P + "user_last_logout_timestamp",
                "Unix timestamp of the most recent iLO logout in the IEL",
                ["product_name", "server_name", "user_login", "method", "source_ip"],
                registry=self.registry,
            ),
            "user_last_failed_login_timestamp": Gauge(
                self.P + "user_last_failed_login_timestamp",
                "Unix timestamp of the most recent failed iLO login in the IEL",
                ["product_name", "server_name", "user_login", "method", "source_ip"],
                registry=self.registry,
            ),
            "login_log_scrape_success": Gauge(
                self.P + "login_log_scrape_success",
                "1 if iLO event log login events were scraped, else 0",
                ["product_name", "server_name"],
                registry=self.registry,
            ),
        }
        BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    def watch_health_at_glance(self):
        health_at_glance = self.embedded_health["health_at_a_glance"]
        if health_at_glance is not None:
            for key, value in health_at_glance.items():
                for status in value.items():
                    if status[0] == "status":
                        health = status[1].upper()
                        self.gauges[key].labels(
                            product_name=self.product_name, server_name=self.server_name
                        ).set(translate(health))

    def watch_temperature(self):
        temperature_values = self.embedded_health.get("temperature", {})
        if temperature_values is not None:
            for t_key, t_value in temperature_values.items():
                s_name = t_key
                s_value = t_value.get("currentreading", "N/A")
                if type(s_value[0]) is int:
                    self.gauges["temperature_value"].labels(
                        product_name=self.product_name,
                        server_name=self.server_name,
                        sensor=s_name,
                    ).set(int(s_value[0]))

    def watch_processor(self):
        processors_values = self.embedded_health.get("processors", {})
        if processors_values is not None:
            for cpu in processors_values.values():
                self.gauges["processor_detail"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    cpu_id=cpu["label"].split()[1],
                    name=cpu["name"].strip(),
                    status=cpu["status"],
                    speed=cpu["speed"],
                ).set(1 if "OK" in cpu["status"] else 0)

    def watch_memory(self):
        memory_values = self.embedded_health.get("memory", {})
        if memory_values and "memory_details_summary" in memory_values:
            for cpu_idx, cpu in memory_values["memory_details_summary"].items():
                total_memory_size = (
                    0
                    if (cpu["total_memory_size"] == "N/A")
                    else int(cpu["total_memory_size"].split()[0])
                )
                self.gauges["memory_detail"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    cpu_id=cpu_idx.split("_")[1],
                    operating_frequency=cpu["operating_frequency"],
                    operating_voltage=cpu["operating_voltage"],
                ).set(total_memory_size)

    def watch_fan(self):
        fan_values = self.embedded_health.get("fans", {})
        if fan_values is not None:
            for f_key, f_value in fan_values.items():
                s_name = f_key
                s_speed = f_value.get("speed", "N/A")
                s_status = f_value.get("status", "N/A")
                if type(s_speed[0]) is int:
                    self.gauges["fan_speed"].labels(
                        product_name=self.product_name,
                        server_name=self.server_name,
                        fan=s_name,
                    ).set(int(s_speed[0]))
                self.gauges["fan"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    fan=s_name,
                ).set(translate(s_status))

    def watch_ps(self):
        ps_values = self.embedded_health.get("power_supplies", {})
        if ps_values is not None:
            for p_key, p_value in ps_values.items():
                s_name = p_key
                s_value = p_value.get("status", "ABSENT")
                capacity_w = (
                    0
                    if p_value.get("capacity") == "N/A"
                    else int(p_value.get("capacity").split()[0])
                )
                self.gauges["power_supply"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    capacity_w=capacity_w,
                    ps=s_name,
                ).set(translate(s_value))

        ps_readings_values = self.embedded_health.get("power_supply_summary", {})
        if ps_readings_values is not None:
            if "present_power_reading" in ps_readings_values:
                # TODO: implement error handling
                readings = ps_readings_values["present_power_reading"]
                self.gauges["power_supplies_readings"].labels(
                    product_name=self.product_name, server_name=self.server_name
                ).set(int(readings.split()[0]))

    def watch_battery(self):
        power_supplies = self.embedded_health.get("power_supplies", {})
        if power_supplies is not None:
            if "Battery 1" in power_supplies:
                batt = power_supplies["Battery 1"]
                label_b = batt["label"]
                present_b = batt["present"]
                status_b = batt["status"]
                model_b = batt["model"]
                spare_b = batt["spare"]
                serial_number_b = batt["serial_number"]
                capacity_b = batt["capacity"]
                firmware_version_b = batt["firmware_version"]

                self.gauges["battery_detail"].labels(
                    label=label_b,
                    present=present_b,
                    status=status_b,
                    model=model_b,
                    spare=spare_b,
                    serial_number=serial_number_b,
                    capacity=capacity_b,
                    firmware_version=firmware_version_b,
                    product_name=self.product_name,
                    server_name=self.server_name,
                ).set(0)

    def watch_disks(self):
        storage_health = self.embedded_health.get("storage", {})
        if storage_health is not None:
            for c_key, c_value in storage_health.items():
                c_model = c_key + ", " + c_value.get("model", "")
                cache_health = c_value.get("cache_module_status", "absent")
                self.gauges["storage_cache_health"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    controller=c_model,
                ).set(translate(cache_health))
                controller_health = c_value.get("controller_status", "unknown")
                self.gauges["storage_controller_health"].labels(
                    product_name=self.product_name,
                    server_name=self.server_name,
                    controller=c_model,
                ).set(translate(controller_health))
                e_key = 0
                enlist = c_value.get("drive_enclosures", [])
                if enlist is not None:
                    for e_value in enlist:
                        enclosure_health = e_value.get("status", "unknown")
                        self.gauges["storage_enclosure_health"].labels(
                            product_name=self.product_name,
                            server_name=self.server_name,
                            controller=c_model,
                            enc=e_key,
                        ).set(translate(enclosure_health))
                        e_key = e_key + 1
                ld_list = c_value.get("logical_drives", [])
                if ld_list is not None:
                    ld_key = 0
                    for ld_value in ld_list:
                        ld_status = ld_value.get("status", "unknown")
                        ld_name = (
                            "LD_"
                            + str(ld_key)
                            + ", "
                            + ld_value.get("capacity", "")
                            + ", "
                            + ld_value.get("fault_tolerance", "")
                        )
                        self.gauges["storage_ld_health"].labels(
                            product_name=self.product_name,
                            server_name=self.server_name,
                            controller=c_model,
                            logical_drive=ld_name,
                        ).set(translate(ld_status))

                        pd_list = ld_value.get("physical_drives", [])
                        if pd_list is not None:
                            pd_key = 0
                            for pd_value in pd_list:
                                pd_status = pd_value.get("status", "unknown")
                                pd_name = (
                                    pd_value.get("model", "")
                                    + ", "
                                    + pd_value.get("capacity", "")
                                    + ", "
                                    + pd_value.get("location", "N" + str(pd_key))
                                )
                                self.gauges["storage_pd_health"].labels(
                                    product_name=self.product_name,
                                    server_name=self.server_name,
                                    controller=c_model,
                                    logical_drive=ld_name,
                                    physical_drive=pd_name,
                                ).set(translate(pd_status))
                                pd_key = pd_key + 1
                        ld_key = ld_key + 1

    def _host_labels(self):
        return {"product_name": self.product_name, "server_name": self.server_name}

    def watch_users(self, ilo):
        host = self._host_labels()
        users = None
        try:
            users = ilo.get_all_user_info()
        except Exception as e:
            print_err("get_all_user_info failed: {}".format(e))
            try:
                logins = ilo.get_all_users() or []
                users = {
                    login: {"user_login": login, "user_name": login} for login in logins
                }
            except Exception as e2:
                print_err("get_all_users failed: {}".format(e2))
                self.gauges["user_scrape_success"].labels(**host).set(0)
                return

        if isinstance(users, list):
            users = {
                u.get("user_login"): u
                for u in users
                if isinstance(u, dict) and u.get("user_login")
            }
        if not isinstance(users, dict):
            users = {}

        self.gauges["user_count"].labels(**host).set(len(users))
        for login, info in users.items():
            if not login:
                continue
            if not isinstance(info, dict):
                info = {"user_login": login}
            user_name = info.get("user_name") or login
            self.gauges["user_info"].labels(
                user_login=login, user_name=user_name, **host
            ).set(1)
            for key, value in info.items():
                if not str(key).endswith("_priv"):
                    continue
                privilege = _PRIV_LABELS.get(key, str(key)[: -len("_priv")])
                self.gauges["user_privilege"].labels(
                    user_login=login, privilege=privilege, **host
                ).set(_as_bool_int(value))
        self.gauges["user_scrape_success"].labels(**host).set(1)

    def watch_login_logs(self, ilo):
        host = self._host_labels()
        try:
            events = ilo.get_ilo_event_log() or []
        except Exception as e:
            print_err("get_ilo_event_log failed: {}".format(e))
            self.gauges["login_log_scrape_success"].labels(**host).set(0)
            return

        if isinstance(events, dict):
            events = events.get("event") or [events]
        if not isinstance(events, list):
            events = []

        counts = {}
        last_by_result = {"success": {}, "logout": {}, "failure": {}}

        for event in events:
            if not isinstance(event, dict):
                continue
            parsed = parse_login_event(event.get("description") or "")
            if not parsed:
                continue
            user = parsed["user"] or "unknown"
            method = parsed["method"] or "unknown"
            result = parsed["result"]
            ip = parsed["source_ip"] or ""
            try:
                n = int(event.get("count") or 1)
            except (TypeError, ValueError):
                n = 1
            key = (user, method, result)
            counts[key] = counts.get(key, 0) + n

            ts = parse_ilo_timestamp(event.get("last_update")) or parse_ilo_timestamp(
                event.get("initial_update")
            )
            if ts is None:
                continue
            dest = last_by_result.get(result)
            if dest is None:
                continue
            prev = dest.get(user)
            if prev is None or ts >= prev[0]:
                dest[user] = (ts, method, ip)

        for (user, method, result), n in counts.items():
            self.gauges["user_login_events"].labels(
                user_login=user, method=method, result=result, **host
            ).set(n)

        gauge_by_result = {
            "success": "user_last_login_timestamp",
            "logout": "user_last_logout_timestamp",
            "failure": "user_last_failed_login_timestamp",
        }
        for result, per_user in last_by_result.items():
            gauge_name = gauge_by_result[result]
            for user, (ts, method, ip) in per_user.items():
                self.gauges[gauge_name].labels(
                    user_login=user, method=method, source_ip=ip, **host
                ).set(ts)

        self.gauges["login_log_scrape_success"].labels(**host).set(1)

    def return_error(self):
        self.send_response(500)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(bytes("Error(s) detected" + "\n", "utf-8"))

    def do_GET(self):  # noqa: C901
        """
        Process GET request

        :return: Response with Prometheus metrics
        """
        # this will be used to return the total amount of time the request took
        start_time = time.time()

        # Create a metric to track time spent and requests made.
        request_time = Summary(
            self.P + "request_processing_seconds",
            "Time spent processing request",
            registry=self.registry,
        )

        # get parameters from the URL
        url = urlparse(self.path)
        # following boolean will be passed to True if an error is detected during the argument parsing
        error_detected = False
        query_components = parse_qs(urlparse(self.path).query)

        if url.path == self.server.endpoint:
            ilo_host = None
            ilo_port = None
            ilo_user = None
            ilo_password = None
            try:
                ilo_host = (
                    query_components.get("ilo_host", [""])[0] or os.environ["ilo_host"]
                )
                ilo_user = (
                    query_components.get("ilo_user", [""])[0] or os.environ["ilo_user"]
                )
                ilo_password = (
                    query_components.get("ilo_password", [""])[0]
                    or os.environ["ilo_password"]
                )
            except KeyError as e:
                print_err("missing parameter %s" % e)
                self.return_error()
                error_detected = True
            try:
                ilo_port = int(
                    query_components.get("ilo_port", [""])[0] or os.environ["ilo_port"]
                )
            except Exception:
                ilo_port = 443

            if ilo_host and ilo_user and ilo_password and ilo_port:
                ilo = None
                try:
                    ilo = hpilo.Ilo(
                        hostname=ilo_host,
                        login=ilo_user,
                        password=ilo_password,
                        port=ilo_port,
                        timeout=30,
                        ssl_verify=False,
                        ssl_context=make_ilo_ssl_context(),
                    )
                except hpilo.IloLoginFailed:
                    print("ILO login failed")
                    self.return_error()
                    return
                except gaierror:
                    print("ILO invalid address or port")
                    self.return_error()
                    return
                except hpilo.IloCommunicationError as e:
                    print(e)
                    self.return_error()
                    return

                # get product and server name
                try:
                    self.product_name = ilo.get_product_name()
                except Exception:
                    self.product_name = "Unknown HP Server"

                try:
                    self.server_name = ilo.get_server_name()
                    if self.server_name == "":
                        self.server_name = ilo_host
                except Exception:
                    self.server_name = ilo_host

                try:
                    # get health, mod by n27051538
                    self.embedded_health = ilo.get_embedded_health()
                    self.watch_health_at_glance()
                    self.watch_disks()
                    self.watch_temperature()
                    self.watch_fan()
                    self.watch_ps()
                    self.watch_processor()
                    self.watch_memory()
                    self.watch_battery()

                    try:
                        running = ilo.get_host_power_status()
                        self.gauges["running"].labels(
                            product_name=self.product_name, server_name=self.server_name
                        ).set(translate(running))
                    except Exception:
                        pass

                    # for iLO3 patch network
                    if ilo.get_fw_version()["management_processor"] == "iLO3":
                        print_err("Unknown iLO nic status")
                    else:
                        # get nic information
                        nic_information = (self.embedded_health or {}).get(
                            "nic_information"
                        ) or {}
                        for nic_name, nic in nic_information.items():
                            try:
                                value = [
                                    "OK",
                                    "Disabled",
                                    "Unknown",
                                    "Link Down",
                                ].index(nic["status"])
                            except ValueError:
                                value = 4
                                print_err(
                                    "unrecognised nic status: {}".format(nic["status"])
                                )

                            self.gauges["nic_status"].labels(
                                product_name=self.product_name,
                                server_name=self.server_name,
                                nic_name=nic_name,
                                ip_address=nic["ip_address"],
                            ).set(value)

                    # get firmware version
                    try:
                        fw_version = ilo.get_fw_version()["firmware_version"]
                        self.gauges["firmware_version"].labels(
                            product_name=self.product_name, server_name=self.server_name
                        ).set(fw_version)
                    except Exception:
                        pass

                    try:
                        oa_info = ilo.get_oa_info()
                        self.gauges["oa_info"].labels(
                            product_name=self.product_name,
                            server_name=self.server_name,
                            oa_ip=oa_info.get("ipaddress", ""),
                            encl=oa_info.get("encl", ""),
                            location_bay=oa_info.get("location", ""),
                        ).set(0)
                    except Exception:
                        pass

                    # local iLO accounts and IEL login history; independent of hardware scrape
                    try:
                        self.watch_users(ilo)
                    except Exception as e:
                        print_err("watch_users failed: {}".format(e))
                    try:
                        self.watch_login_logs(ilo)
                    except Exception as e:
                        print_err("watch_login_logs failed: {}".format(e))
                except Exception as e:
                    print_err("ILO scrape failed: {}".format(e))
                    self.return_error()
                    return

                # get the amount of time the request took
                request_time.observe(time.time() - start_time)

                # generate and publish metrics
                metrics = generate_latest(self.registry)
                process_metrics = generate_latest(self.process_registry)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(bytes(metrics))
                self.wfile.write(bytes(process_metrics))

        elif url.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                bytes(
                    """<html>
            <head><title>HP iLO Exporter</title></head>
            <body>
            <h1>HP iLO Exporter</h1>
            <p>Visit <a href="/metrics">Metrics</a> to use.</p>
            </body>
            </html>""",
                    "utf-8",
                )
            )

        elif url.path == "/favicon.ico":
            self.send_response(404)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(bytes("Not found.\n", "utf-8"))

        else:
            if not error_detected:
                self.send_response(404)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(bytes("Not found.\n", "utf-8"))


class ILOExporterServer(object):
    """
    Basic server implementation that exposes metrics to Prometheus
    """

    def __init__(self, address="0.0.0.0", port=8080, endpoint="/metrics"):
        self._address = address
        self._port = port
        self.endpoint = endpoint

    def print_info(self):
        print_err(
            "Starting exporter on: http://{}:{}{}".format(
                self._address, self._port, self.endpoint
            )
        )
        print_err("Press Ctrl+C to quit")

    def run(self):
        self.print_info()

        server = ThreadingHTTPServer((self._address, self._port), RequestHandler)
        server.endpoint = self.endpoint

        pid = None
        try:
            while True:
                server.handle_request()

                # new block waiting for zombies
                for proc in psutil.process_iter():
                    if "hpilo-exporter" in proc.name():
                        pid = proc.pid
                        p = psutil.Process(pid)
                        if p.status() == psutil.STATUS_ZOMBIE:
                            # print("wait for pid: " + str(pid) + " p.status: " + str(  p.status()  )  ) # running, sleeping, zombie
                            if pid != 0:
                                os.waitid(os.P_PID, pid, os.WEXITED)  # terminate zombie
                        pid = None

        except KeyboardInterrupt:
            print_err("Killing exporter")
            server.server_close()
