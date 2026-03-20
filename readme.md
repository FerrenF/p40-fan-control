# Tesla P40 Fan Control w/ Raspberry Pi Zero W + Python

This small project includes:

```relay_control.py``` - A very small webserver built to run on a Raspberry PI Zero W in order to switch on and off a relay gating power to a blower fan.  
```fan_control_automation.py``` - A small python script meant to sit in the background of a running unix installation, turning on and off the fan as GPU temperature and utilization spike.  



## Systemd service
```
***The following example assumes you've installed this to ~./***

[Unit]
Description=FanControl
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/<user>/p40-fan-control/relay_control.py
WorkingDirectory=/home/<user>/p40-fan-control
Restart=always

[Install]
WantedBy=multi-user.target
```
