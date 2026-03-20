#!/usr/bin/env python3

from flask import Flask, jsonify
from gpiozero import OutputDevice
import signal
import sys
import logging
import json



# P40 Project: Relay Controller. Turns the relay controlling power to the fan on and off.
# 
# You can also think of this as: Toggles the fan from low to high.
# 'on' = low, 'off' = high, because the circuit I am using uses a relay with an ACTIVE HIGH


RELAY_PIN = 13
ACTIVE_HIGH = True
PORT = 8080
HOST = "0.0.0.0"
DEBUG = False

LOG_ENABLED = True
LOG_FILE = "relay_server.log"

logger = logging.getLogger(__name__)

if LOG_ENABLED:
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(message)s"
    )

relay = OutputDevice(
    RELAY_PIN,
    active_high=ACTIVE_HIGH,
    initial_value=False
)

app = Flask(__name__)

def log_response(data):
    if LOG_ENABLED:
        logger.info(json.dumps(data))

@app.route("/")
def index():
    data = {
        "relay": "on" if relay.value else "off",
        "endpoints": ["/on", "/off", "/toggle", "/status"]
    }
    log_response(data)
    return jsonify(data)

@app.route("/on")
def relay_on():
    relay.on()
    data = {"relay": "on"}
    log_response(data)
    return jsonify(data)

@app.route("/off")
def relay_off():
    relay.off()
    data = {"relay": "off"}
    log_response(data)
    return jsonify(data)

@app.route("/toggle")
def relay_toggle():
    relay.toggle()
    data = {"relay": "on" if relay.value else "off"}
    log_response(data)
    return jsonify(data)

@app.route("/status")
def relay_status():
    data = {"relay": "on" if relay.value else "off"}
    log_response(data)
    return jsonify(data)

def shutdown_handler(signum, frame):
    relay.off()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

if __name__ == "__main__":
    print(f"Relay web server running on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)
