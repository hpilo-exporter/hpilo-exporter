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
import prometheus_metrics
from prometheus_client import generate_latest, Summary
try:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ForkingMixIn
    from urllib2 import build_opener, Request, HTTPHandler
    from urllib import quote_plus
    from urlparse import parse_qs, urlparse
except ImportError:
    # Python 3
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ForkingMixIn
    from urllib.request import build_opener, Request, HTTPHandler
    from urllib.parse import quote_plus, parse_qs, urlparse

def print_err(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


# Create a metric to track time spent and requests made.
REQUEST_TIME = Summary(
    'request_processing_seconds', 'Time spent processing request')


class ForkingHTTPServer(ForkingMixIn, HTTPServer):
    max_children = 30
    timeout = 30


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
            except gaierror:
                print("ILO invalid address or port")
                self.return_error()
            except hpilo.IloCommunicationError as e:
                print(e)
                return None

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

            # get health
            embedded_health = ilo.get_embedded_health()
            health_at_glance = embedded_health['health_at_a_glance']

            if health_at_glance is not None:
                for key, value in health_at_glance.items():
                    for status in value.items():
                        if status[0] == 'status':
                            gauge = 'hpilo_{}_gauge'.format(key)
                            if status[1].upper() == 'OK':
                                prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                        server_name=server_name).set(0)
                            elif status[1].upper() == 'DEGRADED':
                                prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                        server_name=server_name).set(1)
                            else:
                                prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                        server_name=server_name).set(2)
            #added by Alexander Golikov for disk status capture
            def translate(st):
                if st.upper() == 'OK':
                    return 0
                elif st.upper() == 'DEGRADED':
                    return 1
                else:
                    return 2

            try:
                storage_health = embedded_health.get('storage',{})
                for ckey, cvalue in storage_health.items():
                    cmodel = ckey + ', ' + cvalue.get('model','')
                    cache_health = cvalue.get('cache_module_status','unknown')
                    prometheus_metrics.gauges['hpilo_storage_cache_health_gauge'].labels(product_name=product_name,
                                                                                         server_name=server_name,controller=cmodel).set(translate(cache_health))
                    controller_health = cvalue.get('controller_status','unknown')
                    prometheus_metrics.gauges['hpilo_storage_controller_health_gauge'].labels(product_name=product_name,
                                                                                              server_name=server_name,controller=cmodel).set(translate(controller_health))
                    ekey = 0
                    enlist = cvalue.get('drive_enclosures',[])
                    if enlist is not None:
                        for evalue in enlist:
                            enclosure_health = evalue.get('status','unknown')
                            prometheus_metrics.gauges['hpilo_storage_enclosure_health_gauge'].labels(product_name=product_name,
                                                                                                     server_name=server_name, controller=cmodel, enc=ekey).set(translate(enclosure_health))
                            ekey = ekey + 1
                    ldlist = cvalue.get('logical_drives',[])
                    if ldlist is not None:
                        ldkey=0
                        for ldvalue in ldlist:
                            ld_status = ldvalue.get('status','unknown')
                            ld_name = 'LD_'  + str(ldkey) + ', ' + ldvalue.get('capacity','') + ', ' + ldvalue.get('fault_tolerance','')
                            prometheus_metrics.gauges['hpilo_storage_ld_health_gauge'].labels(product_name=product_name,
                                                                                              server_name=server_name, controller=cmodel, logical_drive=ld_name).set(translate(ld_status))

                            pdlist=ldvalue.get('physical_drives',[])
                            if pdlist is not None:
                                pdkey=0
                                for pdvalue in pdlist:
                                    pd_status = pdvalue.get('status','unknown')
                                    pd_name = pdvalue.get('model','') + ', ' + pdvalue.get('capacity','') + ', ' + pdvalue.get('location','N'+str(pdkey))
                                    prometheus_metrics.gauges['hpilo_storage_pd_health_gauge'].labels(product_name=product_name,
                                                                                                      server_name=server_name, controller=cmodel, logical_drive=ld_name, physical_drive=pd_name).set(translate(pd_status))
                                    pdkey = pdkey + 1
                            ldkey = ldkey + 1

            except ValueError as e:
                print(e)
                return None
            #end of addon


            #for iLO3 patch network
            if ilo.get_fw_version()["management_processor"] == 'iLO3':
                print_err('Unknown iLO nic status')
            else:
                # get nic information
                for nic_name,nic in embedded_health['nic_information'].items():
                    try:
                        value = ['OK','Disabled','Unknown','Link Down'].index(nic['status'])
                    except ValueError:
                        value = 4
                        print_err('unrecognised nic status: {}'.format(nic['status']))

                    prometheus_metrics.hpilo_nic_status_gauge.labels(product_name=product_name,
                                                                     server_name=server_name,
                                                                     nic_name=nic_name,
                                                                     ip_address=nic['ip_address']).set(value)

            # get firmware version
            fw_version = ilo.get_fw_version()["firmware_version"]
            # prometheus_metrics.hpilo_firmware_version.set(fw_version)
            prometheus_metrics.hpilo_firmware_version.labels(product_name=product_name,
                                                             server_name=server_name).set(fw_version)

            # get the amount of time the request took
            REQUEST_TIME.observe(time.time() - start_time)

            # generate and publish metrics
            metrics = generate_latest(prometheus_metrics.registry)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(metrics)

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

        server = ForkingHTTPServer((self._address, self._port), RequestHandler)
        server.endpoint = self.endpoint

        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            print_err("Killing exporter")
            server.server_close()