#!/usr/bin/env python3

from flask import Flask, jsonify
from gpiozero import OutputDevice
import signal
import sys

RELAY_PIN = 13
ACTIVE_HIGH = True
PORT = 8080
HOST = "0.0.0.0"
DEBUG = False


# P40 Project: Relay Controller. Turns the relay controlling power to the fan on and off.
# 
# You can also think of this as: Toggles the fan from low to high.
# 'on' = low, 'off' = high, because the circuit I am using uses a relay with an ACTIVE HIGH



relay = OutputDevice(
    RELAY_PIN,
    active_high=ACTIVE_HIGH,
    initial_value=False
)

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({
        "relay": "on" if relay.value else "off",
        "endpoints": ["/on", "/off", "/toggle", "/status"]
    })

@app.route("/on")
def relay_on():
    relay.on()
    return jsonify({"relay": "on"})

@app.route("/off")
def relay_off():
    relay.off()
    return jsonify({"relay": "off"})

@app.route("/toggle")
def relay_toggle():
    relay.toggle()
    return jsonify({"relay": "on" if relay.value else "off"})

@app.route("/status")
def relay_status():
    return jsonify({"relay": "on" if relay.value else "off"})

def shutdown_handler(signum, frame):
    relay.off()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

if __name__ == "__main__":
    print(f"Relay web server running on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)