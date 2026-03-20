#!/usr/bin/env python3
"""
GPU Fan Controller for P40 with Raspberry Pi relay control.

Controls a blower fan via a Pi-controlled relay based on GPU utilization
and temperature. Designed to run as a systemd service.
"""

import subprocess
import logging
import time
import os
import signal
import sys
from dataclasses import dataclass, field
from typing import Protocol, Optional
from pathlib import Path
from enum import Enum
import argparse

import requests


class RelayState(Enum):
    ENERGIZED = "off"   # Relay coil energized -> fan ON
    RELEASED = "on"     # Relay coil released -> fan OFF


@dataclass
class FanControllerConfig:
    """Configuration for the fan controller."""
    pi_address: str = "192.168.1.114"
    pi_port: int = 8080
    
    utilization_threshold: int = 15      # GPU usage % to trigger fan
    temperature_threshold: int = 65      # Celsius to trigger fan

    check_interval: int = 20             # Seconds between checks
    minimum_fan_runtime: int = 120       # Minimum seconds fan stays on once activated
    request_timeout: int = 5             # HTTP request timeout
    
    fan_timeout_kill_threshold: int = 3  # Failed checks before killing GPU processes
    
    @property
    def base_url(self) -> str:
        return f"http://{self.pi_address}:{self.pi_port}"


class GPUInterface(Protocol):
    """Protocol for GPU operations. Allows mocking in tests."""
    
    def get_utilization(self) -> int:
        """Return GPU utilization percentage (0-100)."""
        ...

    def get_temperature(self) -> int:
        """Return GPU temperature in Celsius."""
        ...
    
    def get_processes(self) -> list[dict]:
        """Return list of processes using the GPU."""
        ...
    
    def kill_process(self, pid: int) -> bool:
        """Kill a process by PID. Returns True if successful."""
        ...


class RelayInterface(Protocol):
    """Protocol for relay control. Allows mocking in tests."""
    
    def get_status(self) -> Optional[RelayState]:
        """Get current relay state. Returns None on communication failure."""
        ...
    
    def set_state(self, state: RelayState) -> bool:
        """Set relay state. Returns True if successful."""
        ...


class NvidiaSmiGPU:
    def get_utilization(self) -> int:
        result = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],capture_output=True, text=True, check=True)
        return int(result.stdout.strip())
    
    def get_temperature(self) -> int:
        result = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],capture_output=True, text=True, check=True)
        return int(result.stdout.strip())
    
    def get_processes(self) -> list[dict]:
        result = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'],capture_output=True, text=True, check=True)
        
        processes = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split(',')
            if len(parts) >= 3:
                processes.append({'pid': int(parts[0].strip()), 'process_name': parts[1].strip(), 'used_memory': parts[2].strip()})
        return processes
    
    def kill_process(self, pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
                return True
            except OSError:
                return False


class PiRelay:
    def __init__(self, config: FanControllerConfig):
        self.config = config
    
    def get_status(self) -> Optional[RelayState]:
        try:
            response = requests.get( f"{self.config.base_url}/status", timeout=self.config.request_timeout)
            response.raise_for_status()
            data = response.json()
            relay_value = data.get('relay')
            if relay_value == RelayState.ENERGIZED.value:
                return RelayState.ENERGIZED
            elif relay_value == RelayState.RELEASED.value:
                return RelayState.RELEASED
            return None
        except (requests.RequestException, KeyError, ValueError):
            return None
    
    def set_state(self, state: RelayState) -> bool:
        try:
            response = requests.post( f"{self.config.base_url}/control", json={'status': state.value}, timeout=self.config.request_timeout)
            return response.status_code == 200
        except requests.RequestException:
            return False


@dataclass
class ControllerState:
    """Mutable state for the controller."""
    fan_activated_at: Optional[float] = None
    consecutive_timeouts: int = 0
    process_kill_sent: bool = False
    shutdown_requested: bool = False


class FanController:
    """Main controller coordinating GPU monitoring and fan control."""
    
    def __init__( self, config: FanControllerConfig, gpu: GPUInterface, relay: RelayInterface, logger: Optional[logging.Logger] = None):
        self.config = config
        self.gpu = gpu
        self.relay = relay
        self.logger = logger or logging.getLogger(__name__)
        self.state = ControllerState()
    
    def should_fan_be_on(self, utilization: int, temperature: int) -> bool:
        """Determine if fan should be running based on current conditions."""
        return ( utilization > self.config.utilization_threshold or temperature >= self.config.temperature_threshold)
    
    def is_minimum_runtime_satisfied(self) -> bool:
        """Check if minimum fan runtime has elapsed since activation."""
        if self.state.fan_activated_at is None:
            return True
        elapsed = time.time() - self.state.fan_activated_at
        return elapsed >= self.config.minimum_fan_runtime
    
    def handle_relay_timeout(self) -> None:
        """Handle communication failure with relay controller."""
        self.state.consecutive_timeouts += 1
        self.logger.warning( f"Relay communication failed. Consecutive failures: {self.state.consecutive_timeouts}" )
        
        if self.state.consecutive_timeouts >= self.config.fan_timeout_kill_threshold:
            if not self.state.process_kill_sent:
                self.logger.error(f"Fan unresponsive for {self.state.consecutive_timeouts} checks. Killing GPU processes as safety measure.")
                self._kill_all_gpu_processes()
                self.state.process_kill_sent = True
    
    def handle_relay_success(self) -> None:
        """Reset timeout tracking on successful communication."""
        if self.state.consecutive_timeouts > 0:
            self.logger.info("Relay communication restored.")
        self.state.consecutive_timeouts = 0
        self.state.process_kill_sent = False
    
    def _kill_all_gpu_processes(self) -> bool:
        """Attempt to kill all GPU processes. Returns True if all killed."""
        processes = self.gpu.get_processes()
        all_killed = True
        
        for proc in processes:
            self.logger.warning(f"Killing GPU process: PID={proc['pid']} ({proc['process_name']})")
            if not self.gpu.kill_process(proc['pid']):
                self.logger.error(f"Failed to kill PID {proc['pid']}")
                all_killed = False
        
        return all_killed
    
    def turn_fan_on(self) -> bool:
        """Turn fan on and record activation time."""
        success = self.relay.set_state(RelayState.ENERGIZED)
        if success and self.state.fan_activated_at is None:
            self.state.fan_activated_at = time.time()
        return success
    
    def turn_fan_off(self) -> bool:
        """Turn fan off and clear activation time."""
        success = self.relay.set_state(RelayState.RELEASED)
        if success:
            self.state.fan_activated_at = None
        return success
    
    def run_single_check(self) -> None:
        """Execute one monitoring cycle."""
        utilization = self.gpu.get_utilization()
        temperature = self.gpu.get_temperature()
        relay_status = self.relay.get_status()
        
        if relay_status is None:
            self.handle_relay_timeout()
            return
        
        self.handle_relay_success()
        
        fan_is_on = (relay_status == RelayState.ENERGIZED)
        fan_should_be_on = self.should_fan_be_on(utilization, temperature)
        
        if fan_should_be_on and not fan_is_on:
            self.logger.info( f"GPU active - Utilization: {utilization}%, Temp: {temperature}°C. Turning fan ON.")
            self.turn_fan_on()
        
        elif not fan_should_be_on and fan_is_on:
            if self.is_minimum_runtime_satisfied():
                self.logger.info( f"GPU idle - Utilization: {utilization}%, Temp: {temperature}°C. Turning fan OFF.")
                self.turn_fan_off()
            else:
                remaining = self.config.minimum_fan_runtime - (time.time() - self.state.fan_activated_at)
                self.logger.debug(f"GPU idle but minimum runtime not met. {remaining:.0f}s remaining.")
    
    def run(self) -> None:
        """Main monitoring loop."""
        self.logger.info("Starting GPU fan controller")
        
        while not self.state.shutdown_requested:
            try:
                self.run_single_check()
            except subprocess.CalledProcessError as e:
                self.logger.error(f"nvidia-smi failed: {e}")
            except Exception as e:
                self.logger.exception(f"Unexpected error in monitoring loop: {e}")
            
            time.sleep(self.config.check_interval)
        
        self.logger.info("Shutdown requested. Turning fan off.")
        self.turn_fan_off()
    
    def request_shutdown(self) -> None:
        """Signal the controller to stop."""
        self.state.shutdown_requested = True


def setup_logging(log_file: Optional[Path] = None, verbose: bool = False) -> logging.Logger:
    """Configure logging for the application."""
    logger = logging.getLogger("gpu_fan_controller")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


def main():
    parser = argparse.ArgumentParser(description="GPU Fan Controller")
    parser.add_argument("--pi-address", default="192.168.1.114", help="Pi relay controller IP")
    parser.add_argument("--pi-port", type=int, default=8080, help="Pi relay controller port")
    parser.add_argument("--util-threshold", type=int, default=15, help="GPU utilization threshold %%")
    parser.add_argument("--temp-threshold", type=int, default=65, help="Temperature threshold (Celsius)")
    parser.add_argument("--check-interval", type=int, default=20, help="Check interval (seconds)")
    parser.add_argument("--min-runtime", type=int, default=120, help="Minimum fan runtime (seconds)")
    parser.add_argument("--log-file", type=Path, help="Log file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    config = FanControllerConfig(
        pi_address=args.pi_address,
        pi_port=args.pi_port,
        utilization_threshold=args.util_threshold,
        temperature_threshold=args.temp_threshold,
        check_interval=args.check_interval,
        minimum_fan_runtime=args.min_runtime,
    )
    
    logger = setup_logging(args.log_file, args.verbose)
    
    gpu = NvidiaSmiGPU()
    relay = PiRelay(config)
    controller = FanController(config, gpu, relay, logger)
    
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        controller.request_shutdown()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    controller.run()


if __name__ == "__main__":
    main()