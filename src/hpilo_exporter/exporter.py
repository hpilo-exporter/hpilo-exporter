"""
Pulls data from specified iLO and presents as Prometheus metrics
"""
from __future__ import print_function
from _socket import gaierror
import sys
import hpilo
import ssl
import time
import os
from prometheus_client import generate_latest, Summary, Gauge, CollectorRegistry, REGISTRY

try:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urllib2 import build_opener, Request, HTTPHandler
    from urllib import quote_plus
    from urlparse import parse_qs, urlparse
except ImportError:
    # Python 3
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.request import build_opener, Request, HTTPHandler
    from urllib.parse import quote_plus, parse_qs, urlparse


def print_err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    max_children = 30
    timeout = 30


def translate(st):
    if st.upper() == 'OK':
        return 0
    elif st.upper() == 'DEGRADED':
        return 1
    elif st.upper() == 'ABSENT':
        return -1
    else:
        return 2


def watch_health_at_glance(embedded_health, gauges, product_name, server_name):
    health_at_glance = embedded_health['health_at_a_glance']
    if health_at_glance is not None:
        for key, value in health_at_glance.items():
            for status in value.items():
                if status[0] == 'status':
                    gauge = 'hpilo_{}_gauge'.format(key)
                    health = status[1].upper()
                    gauges[gauge].labels(product_name=product_name,
                                         server_name=server_name).set(translate(health))


def watch_disks(embedded_health, gauges, product_name, server_name):
    storage_health = embedded_health.get('storage', {})
    for c_key, cvalue in storage_health.items():
        c_model = c_key + ', ' + cvalue.get('model', '')
        cache_health = cvalue.get('cache_module_status', 'absent')
        gauges['hpilo_storage_cache_health_gauge'].labels(product_name=product_name,
                                                          server_name=server_name,
                                                          controller=c_model).set(
            translate(cache_health))
        controller_health = cvalue.get('controller_status', 'unknown')
        gauges['hpilo_storage_controller_health_gauge'].labels(product_name=product_name,
                                                               server_name=server_name,
                                                               controller=c_model).set(
            translate(controller_health))
        e_key = 0
        enlist = cvalue.get('drive_enclosures', [])
        if enlist is not None:
            for e_value in enlist:
                enclosure_health = e_value.get('status', 'unknown')
                gauges['hpilo_storage_enclosure_health_gauge'].labels(product_name=product_name,
                                                                      server_name=server_name,
                                                                      controller=c_model,
                                                                      enc=e_key).set(
                    translate(enclosure_health))
                e_key = e_key + 1
        ld_list = cvalue.get('logical_drives', [])
        if ld_list is not None:
            ld_key = 0
            for ld_value in ld_list:
                ld_status = ld_value.get('status', 'unknown')
                ld_name = 'LD_' + str(ld_key) + ', ' + ld_value.get('capacity', '') + ', ' + ld_value.get(
                    'fault_tolerance', '')
                gauges['hpilo_storage_ld_health_gauge'].labels(product_name=product_name,
                                                               server_name=server_name,
                                                               controller=c_model,
                                                               logical_drive=ld_name).set(
                    translate(ld_status))

                pd_list = ld_value.get('physical_drives', [])
                if pd_list is not None:
                    pd_key = 0
                    for pd_value in pd_list:
                        pd_status = pd_value.get('status', 'unknown')
                        pd_name = pd_value.get('model', '') + ', ' + pd_value.get('capacity', '') + ', ' + \
                                  pd_value.get('location', 'N' + str(pd_key))
                        gauges['hpilo_storage_pd_health_gauge'].labels(product_name=product_name,
                                                                       server_name=server_name,
                                                                       controller=c_model,
                                                                       logical_drive=ld_name,
                                                                       physical_drive=pd_name).set(
                            translate(pd_status))
                        pd_key = pd_key + 1
                ld_key = ld_key + 1


def watch_temperature(embedded_health, gauges, product_name, server_name):
    temperature_values = embedded_health.get('temperature', {})
    for t_key, t_value in temperature_values.items():
        s_name = t_key
        s_value = t_value.get('currentreading', 'N/A')
        if type(s_value[0]) is int:
            gauges['hpilo_temperature_value_gauge'].labels(product_name=product_name, server_name=server_name,
                                                           sensor=s_name).set(int(s_value[0]))


class RequestHandler(BaseHTTPRequestHandler):
    """
    Endpoint handler
    """

    def return_error(self):
        self.send_response(500)
        self.end_headers()

    def do_GET(self):
        """
        Process GET request

        :return: Response with Prometheus metrics
        """
        # this will be used to return the total amount of time the request took
        start_time = time.time()
        registry = None
        registry = CollectorRegistry(self)
        Process_Registry = REGISTRY

        # Create a metric to track time spent and requests made.
        REQUEST_TIME = Summary('hpilo_request_processing_seconds', 'Time spent processing request', registry=registry)

        hpilo_vrm_gauge = Gauge('hpilo_vrm', 'HP iLO vrm status', ["product_name", "server_name"], registry=registry)
        hpilo_drive_gauge = Gauge('hpilo_drive', 'HP iLO drive status', ["product_name", "server_name"],
                                  registry=registry)
        hpilo_battery_gauge = Gauge('hpilo_battery', 'HP iLO battery status', ["product_name", "server_name"],
                                    registry=registry)
        hpilo_storage_gauge = Gauge('hpilo_storage', 'HP iLO storage status', ["product_name", "server_name"],
                                    registry=registry)
        hpilo_fans_gauge = Gauge('hpilo_fans', 'HP iLO fans status', ["product_name", "server_name"], registry=registry)
        hpilo_bios_hardware_gauge = Gauge('hpilo_bios_hardware', 'HP iLO bios_hardware status',
                                          ["product_name", "server_name"], registry=registry)
        hpilo_memory_gauge = Gauge('hpilo_memory', 'HP iLO memory status', ["product_name", "server_name"],
                                   registry=registry)
        hpilo_power_supplies_gauge = Gauge('hpilo_power_supplies', 'HP iLO power_supplies status', ["product_name",
                                                                                                    "server_name"],
                                           registry=registry)
        hpilo_processor_gauge = Gauge('hpilo_processor', 'HP iLO processor status', ["product_name", "server_name"],
                                      registry=registry)
        hpilo_network_gauge = Gauge('hpilo_network', 'HP iLO network status', ["product_name", "server_name"],
                                    registry=registry)
        hpilo_temperature_gauge = Gauge('hpilo_temperature', 'HP iLO temperature status',
                                        ["product_name", "server_name"], registry=registry)
        hpilo_firmware_version = Gauge('hpilo_firmware_version', 'HP iLO firmware version',
                                       ["product_name", "server_name"], registry=registry)
        hpilo_nic_status_gauge = Gauge('hpilo_nic_status', 'HP iLO NIC status',
                                       ["product_name", "server_name", "nic_name", "ip_address"], registry=registry)
        hpilo_storage_cache_health_gauge = Gauge('hpilo_storage_cache_health', 'Cache Module status',
                                                 ["product_name", "server_name", "controller"], registry=registry)
        hpilo_storage_controller_health_gauge = Gauge('hpilo_storage_controller_health', 'Controller status',
                                                      ["product_name", "server_name", "controller"], registry=registry)
        hpilo_storage_enclosure_health_gauge = Gauge('hpilo_storage_enclosure_health', 'Enclosure status',
                                                     ["product_name", "server_name", "controller", "enc"],
                                                     registry=registry)
        hpilo_storage_ld_health_gauge = Gauge('hpilo_storage_ld_health', 'LD status',
                                              ["product_name", "server_name", "controller", "logical_drive"],
                                              registry=registry)
        hpilo_storage_pd_health_gauge = Gauge('hpilo_storage_pd_health', 'PD status',
                                              ["product_name", "server_name", "controller", "logical_drive",
                                               "physical_drive"], registry=registry)
        hpilo_temperature_value_gauge = Gauge('hpilo_temperature_value', 'Temperature value',
                                              ["product_name", "server_name", "sensor"], registry=registry)

        gauges = {
            'hpilo_vrm_gauge': hpilo_vrm_gauge,
            'hpilo_drive_gauge': hpilo_drive_gauge,
            'hpilo_battery_gauge': hpilo_battery_gauge,
            'hpilo_storage_gauge': hpilo_storage_gauge,
            'hpilo_fans_gauge': hpilo_fans_gauge,
            'hpilo_bios_hardware_gauge': hpilo_bios_hardware_gauge,
            'hpilo_memory_gauge': hpilo_memory_gauge,
            'hpilo_power_supplies_gauge': hpilo_power_supplies_gauge,
            'hpilo_processor_gauge': hpilo_processor_gauge,
            'hpilo_network_gauge': hpilo_network_gauge,
            'hpilo_temperature_gauge': hpilo_temperature_gauge,
            'hpilo_firmware_version': hpilo_firmware_version,
            'hpilo_nic_status_gauge': hpilo_nic_status_gauge,
            'hpilo_storage_cache_health_gauge': hpilo_storage_cache_health_gauge,
            'hpilo_storage_controller_health_gauge': hpilo_storage_controller_health_gauge,
            'hpilo_storage_enclosure_health_gauge': hpilo_storage_enclosure_health_gauge,
            'hpilo_storage_ld_health_gauge': hpilo_storage_ld_health_gauge,
            'hpilo_storage_pd_health_gauge': hpilo_storage_pd_health_gauge,
            'hpilo_temperature_value_gauge': hpilo_temperature_value_gauge,
        }

        # get parameters from the URL
        url = urlparse(self.path)
        # following boolean will be passed to True if an error is detected during the argument parsing
        error_detected = False
        query_components = parse_qs(urlparse(self.path).query)

        ilo_host = None
        ilo_port = None
        ilo_user = None
        ilo_password = None
        try:
            ilo_host = query_components.get('ilo_host', [''])[0] or os.environ['ilo_host']
            ilo_user = query_components.get('ilo_user', [''])[0] or os.environ['ilo_user']
            ilo_password = query_components.get('ilo_password', [''])[0] or os.environ['ilo_password']
        except KeyError as e:
            print_err("missing parameter %s" % e)
            self.return_error()
            error_detected = True
        try:
            ilo_port = int(query_components.get('ilo_port', [''])[0] or os.environ['ilo_port'])
        except KeyError as e:
            ilo_port = 443

        if url.path == self.server.endpoint and ilo_host and ilo_user and ilo_password and ilo_port:
            ilo = None
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            # Sadly, ancient iLO's aren't dead yet, so let's enable sslv3 by default
            ssl_context.options &= ~ssl.OP_NO_SSLv3
            ssl_context.check_hostname = False
            ssl_context.set_ciphers(('ECDH+AESGCM:DH+AESGCM:ECDH+AES256:DH+AES256:ECDH+AES128:DH+AES:ECDH+HIGH:'
                                     'DH+HIGH:ECDH+3DES:DH+3DES:RSA+AESGCM:RSA+AES:RSA+HIGH:RSA+3DES:!aNULL:'
                                     '!eNULL:!MD5'))
            try:
                ilo = hpilo.Ilo(hostname=ilo_host,
                                login=ilo_user,
                                password=ilo_password,
                                port=ilo_port, timeout=10, ssl_context=ssl_context)
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
                product_name = ilo.get_product_name()
            except:
                product_name = "Unknown HP Server"

            try:
                server_name = ilo.get_server_name()
                if server_name == "":
                    server_name = ilo_host
            except:
                server_name = ilo_host

            # get health, mod by n27051538
            embedded_health = ilo.get_embedded_health()
            watch_health_at_glance(embedded_health, gauges, product_name, server_name)
            watch_disks(embedded_health, gauges, product_name, server_name)
            watch_temperature(embedded_health, gauges, product_name, server_name)

            # for iLO3 patch network
            if ilo.get_fw_version()["management_processor"] == 'iLO3':
                print_err('Unknown iLO nic status')
            else:
                # get nic information
                for nic_name, nic in embedded_health['nic_information'].items():
                    try:
                        value = ['OK', 'Disabled', 'Unknown', 'Link Down'].index(nic['status'])
                    except ValueError:
                        value = 4
                        print_err('unrecognised nic status: {}'.format(nic['status']))

                    hpilo_nic_status_gauge.labels(product_name=product_name, server_name=server_name, nic_name=nic_name,
                                                  ip_address=nic['ip_address']).set(value)

            # get firmware version
            fw_version = ilo.get_fw_version()["firmware_version"]
            hpilo_firmware_version.labels(product_name=product_name, server_name=server_name).set(fw_version)

            # get the amount of time the request took
            REQUEST_TIME.observe(time.time() - start_time)

            # generate and publish metrics
            metrics = generate_latest(registry)
            proc_metrics = generate_latest(Process_Registry)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(metrics)
            self.wfile.write(proc_metrics)

        elif url.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write("""<html>
            <head><title>HP iLO Exporter</title></head>
            <body>
            <h1>HP iLO Exporter</h1>
            <p>Visit <a href="/metrics">Metrics</a> to use.</p>
            </body>
            </html>""")

        else:
            if not error_detected:
                self.send_response(404)
            self.end_headers()


class ILOExporterServer(object):
    """
    Basic server implementation that exposes metrics to Prometheus
    """

    def __init__(self, address='0.0.0.0', port=8080, endpoint="/metrics"):
        self._address = address
        self._port = port
        self.endpoint = endpoint

    def print_info(self):
        print_err("Starting exporter on: http://{}:{}{}".format(self._address, self._port, self.endpoint))
        print_err("Press Ctrl+C to quit")

    def run(self):
        self.print_info()

        server = ThreadingHTTPServer((self._address, self._port), RequestHandler)
        server.endpoint = self.endpoint

        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            print_err("Killing exporter")
            server.server_close()
