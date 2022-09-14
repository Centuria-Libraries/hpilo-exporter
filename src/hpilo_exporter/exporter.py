"""
Pulls data from specified iLO and presents as Prometheus metrics
"""
from __future__ import print_function
from _socket import gaierror
import sys
import os
import hpilo
import time
sys.path.append((os.path.dirname(os.path.abspath(__file__))))
import prometheus_metrics
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from socketserver import ForkingMixIn
from prometheus_client import generate_latest, Summary
from urllib.parse import parse_qs
from urllib.parse import urlparse

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
            ilo_host = query_components['ilo_host'][0]
            ilo_port = int(query_components['ilo_port'][0])
            ilo_user = query_components['ilo_user'][0]
            ilo_password = query_components['ilo_password'][0]
        except KeyError as e:
            print_err("missing parameter %s" % e)
            self.return_error()
            error_detected = True

        if url.path == self.server.endpoint and ilo_host and ilo_user and ilo_password and ilo_port:

            ilo = None
            try:
                ilo = hpilo.Ilo(hostname=ilo_host,
                                login=ilo_user,
                                password=ilo_password,
                                port=ilo_port, timeout=10)
            except hpilo.IloLoginFailed:
                print("ILO login failed")
                self.return_error()
            except gaierror:
                print("ILO invalid address or port")
                self.return_error()
            except hpilo.IloCommunicationError as e:
                print(e)

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
                        if status[0] == 'redundancy':
                            gauge = 'hpilo_{}_redundancy_gauge'.format(key)
                            if status[1].upper() == 'REDUNDANT':
                                prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                        server_name=server_name).set(0)
                            else:
                                prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                        server_name=server_name).set(1)

            # get physical drives' status
            storage = embedded_health['storage']
            for key,value in storage.items():
                if "Controller" in key:
                    try:
                        logical_drives = value['logical_drives']

                        for logical_drive in logical_drives:
                            for physical_drive in logical_drive['physical_drives']:
                                if physical_drive['status'] == 'OK':
                                    prometheus_metrics.hpilo_physical_disk_status.labels(product_name=product_name, server_name=server_name, location=physical_drive['location'], serial_number=physical_drive['serial_number'], capacity=physical_drive['marketing_capacity'], media_type=physical_drive['media_type']).set(0)
                                else:
                                    prometheus_metrics.hpilo_physical_disk_status.labels(product_name=product_name, server_name=server_name, location=physical_drive['location'], serial_number=physical_drive['serial_number'], capacity=physical_drive['marketing_capacity'], media_type=physical_drive['media_type']).set(1)
                    except:
                        pass

            # get temperatures
            try:
                temperature = ilo.get_embedded_health()['temperature']
                ambient_temperature_reading = temperature['01-Inlet Ambient']['currentreading'][0]
                prometheus_metrics.hpilo_ambient_temperature_reading.labels(product_name=product_name, server_name=server_name).set(ambient_temperature_reading)

                cpu1_temperature_reading = temperature['02-CPU 1']['currentreading'][0]
                prometheus_metrics.hpilo_cpu1_temperature_reading.labels(product_name=product_name, server_name=server_name).set(cpu1_temperature_reading)

                cpu2_temperature_reading = temperature['03-CPU 2']['currentreading'][0]
                prometheus_metrics.hpilo_cpu2_temperature_reading.labels(product_name=product_name, server_name=server_name).set(cpu2_temperature_reading)
            except:
                pass

            # get firmware version
            fw_version = ilo.get_fw_version()["firmware_version"]
            # prometheus_metrics.hpilo_firmware_version.set(fw_version)
            prometheus_metrics.hpilo_firmware_version.labels(product_name=product_name,
                                                             server_name=server_name).set(fw_version)

            # get power status
            power_status = ilo.get_host_power_status()

            gauge = 'hpilo_{}_gauge'.format('power_status')
            if power_status.upper() == 'ON':
                    prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                            server_name=server_name).set(0)
            else:
                    prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                            server_name=server_name).set(1)

            # get power saver status
            power_saver_status = ilo.get_host_power_saver_status()

            gauge = 'hpilo_{}_gauge'.format('power_saver_status')
            if power_saver_status is not None:
                    if 'host_power_saver' in power_saver_status:
                            if power_saver_status['host_power_saver'].upper() == 'MAX':
                                    prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                            server_name=server_name).set(0)
                            else:
                                    prometheus_metrics.gauges[gauge].labels(product_name=product_name,
                                                                            server_name=server_name).set(1)
            # get power reading
            power_reading = ilo.get_power_readings()["present_power_reading"][0]
            # prometheus_metrics.hpilo_present_power_reading(power_reading)
            prometheus_metrics.hpilo_present_power_reading.labels(product_name=product_name, server_name=server_name).set(power_reading)

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
            server.serve_forever()
        except KeyboardInterrupt:
            print_err("Killing exporter")
            server.server_close()
