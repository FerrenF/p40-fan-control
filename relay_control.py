#!/usr/bin/python

import gpiozero
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib
import threading

# A very small web server that controls a relay gating power to a blower fan on a Tesla P40 GPU.


# GPIO pin the relay is connected to
RELAY_PIN = 17

# create a relay object
relay = gpiozero.OutputDevice(RELAY_PIN, active_high=True, initial_value=False)

# Function to control the relay
def set_relay(status):
    if status:
        print("Setting relay: ON")
        relay.on()
    else:
        print("Setting relay: OFF")
        relay.off()

# Function to check if the relay is on
def is_relay_on():
    return relay.value  # Returns True if the relay is on, False if off

# HTTP request handler
class RelayRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Parse the query parameters
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)
        
        if parsed_path.path == '/control':  # Control relay using 'on' or 'off'
            if 'status' in query:
                status = query['status'][0]
                if status == "on":
                    set_relay(True)
                    relay_status = is_relay_on()
                    self.send_response(200)
                    self.end_headers()
                    if relay_status:
                        self.wfile.write(b'Relay turned ON successfully')
                    else:
                        self.wfile.write(b'Failed to turn ON the relay')
                elif status == "off":
                    set_relay(False)
                    relay_status = is_relay_on()
                    self.send_response(200)
                    self.end_headers()
                    if not relay_status:
                        self.wfile.write(b'Relay turned OFF successfully')
                    else:
                        self.wfile.write(b'Failed to turn OFF the relay')
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'Invalid status. Use "on" or "off".')
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing "status" parameter')

        elif parsed_path.path == '/status':  # Endpoint to check relay status
            relay_status = is_relay_on()
            self.send_response(200)
            self.end_headers()
            if relay_status:
                self.wfile.write(b'Relay is ON')
            else:
                self.wfile.write(b'Relay is OFF')
        
        elif parsed_path.path == '/shutdown':  # Endpoint to shutdown the server
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Shutting down server...')
            threading.Thread(target=self.server.shutdown).start()  # Shut down the server


# Function to run the HTTP server
def run(server_class=HTTPServer, handler_class=RelayRequestHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f'Starting HTTP server on port {port}')
    httpd.serve_forever()

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        print("Cleaning up and turning off the relay...")
        set_relay(False)  # Ensure the relay is off when the server shuts down