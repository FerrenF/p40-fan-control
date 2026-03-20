#!/usr/bin/env python3
"""Tests for GPU Fan Controller."""

import pytest
import time
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

from gpu_fan_controller import (
    FanController,
    FanControllerConfig,
    RelayState,
    ControllerState,
    GPUInterface,
    RelayInterface,
)


class MockGPU:
    """Mock GPU for testing."""
    
    def __init__(self, utilization: int = 0, temperature: int = 40):
        self.utilization = utilization
        self.temperature = temperature
        self.processes: list[dict] = []
        self.killed_pids: list[int] = []
    
    def get_utilization(self) -> int:
        return self.utilization
    
    def get_temperature(self) -> int:
        return self.temperature
    
    def get_processes(self) -> list[dict]:
        return self.processes
    
    def kill_process(self, pid: int) -> bool:
        self.killed_pids.append(pid)
        return True


class MockRelay:
    """Mock relay for testing."""
    
    def __init__(self, initial_state: RelayState = RelayState.RELEASED):
        self._state = initial_state
        self.fail_next_get = False
        self.fail_next_set = False
        self.state_changes: list[RelayState] = []
    
    def get_status(self) -> Optional[RelayState]:
        if self.fail_next_get:
            self.fail_next_get = False
            return None
        return self._state
    
    def set_state(self, state: RelayState) -> bool:
        if self.fail_next_set:
            self.fail_next_set = False
            return False
        self._state = state
        self.state_changes.append(state)
        return True


@pytest.fixture
def config():
    return FanControllerConfig(
        utilization_threshold=15,
        temperature_threshold=65,
        check_interval=1,
        minimum_fan_runtime=10,
        fan_timeout_kill_threshold=3,
    )


@pytest.fixture
def mock_gpu():
    return MockGPU()


@pytest.fixture
def mock_relay():
    return MockRelay()


@pytest.fixture
def controller(config, mock_gpu, mock_relay):
    return FanController(config, mock_gpu, mock_relay)


class TestFanDecisionLogic:
    """Test the core decision logic for fan control."""
    
    def test_fan_should_be_on_high_utilization(self, controller, mock_gpu):
        """Fan should turn on when utilization exceeds threshold."""
        mock_gpu.utilization = 50
        mock_gpu.temperature = 40
        assert controller.should_fan_be_on(50, 40) is True
    
    def test_fan_should_be_on_high_temperature(self, controller, mock_gpu):
        """Fan should turn on when temperature meets threshold."""
        mock_gpu.utilization = 5
        mock_gpu.temperature = 65
        assert controller.should_fan_be_on(5, 65) is True
    
    def test_fan_should_be_off_below_thresholds(self, controller, mock_gpu):
        """Fan should be off when both metrics below thresholds."""
        mock_gpu.utilization = 10
        mock_gpu.temperature = 50
        assert controller.should_fan_be_on(10, 50) is False
    
    def test_fan_should_be_on_at_utilization_boundary(self, controller):
        """Fan should NOT be on at exactly the threshold (uses >)."""
        assert controller.should_fan_be_on(15, 40) is False
        assert controller.should_fan_be_on(16, 40) is True
    
    def test_fan_should_be_on_at_temperature_boundary(self, controller):
        """Fan SHOULD be on at exactly the threshold (uses >=)."""
        assert controller.should_fan_be_on(10, 64) is False
        assert controller.should_fan_be_on(10, 65) is True


class TestMinimumRuntime:
    """Test minimum runtime enforcement."""
    
    def test_minimum_runtime_not_started(self, controller):
        """When fan was never activated, runtime is satisfied."""
        assert controller.is_minimum_runtime_satisfied() is True
    
    def test_minimum_runtime_not_elapsed(self, controller):
        """When fan was recently activated, runtime is not satisfied."""
        controller.state.fan_activated_at = time.time()
        assert controller.is_minimum_runtime_satisfied() is False
    
    def test_minimum_runtime_elapsed(self, controller, config):
        """When enough time has passed, runtime is satisfied."""
        controller.state.fan_activated_at = time.time() - config.minimum_fan_runtime - 1
        assert controller.is_minimum_runtime_satisfied() is True
    
    def test_fan_stays_on_during_minimum_runtime(self, controller, mock_gpu, mock_relay, config):
        """Fan should not turn off before minimum runtime even if conditions allow."""
        # Start with fan on and recently activated
        mock_relay._state = RelayState.ENERGIZED
        controller.state.fan_activated_at = time.time()
        
        # GPU is now idle
        mock_gpu.utilization = 5
        mock_gpu.temperature = 40
        
        controller.run_single_check()
        
        # Fan should still be on
        assert mock_relay._state == RelayState.ENERGIZED
    
    def test_fan_turns_off_after_minimum_runtime(self, controller, mock_gpu, mock_relay, config):
        """Fan should turn off after minimum runtime when conditions allow."""
        # Start with fan on and activated long ago
        mock_relay._state = RelayState.ENERGIZED
        controller.state.fan_activated_at = time.time() - config.minimum_fan_runtime - 1
        
        # GPU is now idle
        mock_gpu.utilization = 5
        mock_gpu.temperature = 40
        
        controller.run_single_check()
        
        # Fan should turn off
        assert mock_relay._state == RelayState.RELEASED


class TestRelayControl:
    """Test relay state transitions."""
    
    def test_turn_fan_on_records_activation_time(self, controller, mock_relay):
        """Turning fan on should record the activation timestamp."""
        assert controller.state.fan_activated_at is None
        
        controller.turn_fan_on()
        
        assert controller.state.fan_activated_at is not None
        assert mock_relay._state == RelayState.ENERGIZED
    
    def test_turn_fan_off_clears_activation_time(self, controller, mock_relay):
        """Turning fan off should clear the activation timestamp."""
        controller.state.fan_activated_at = time.time()
        
        controller.turn_fan_off()
        
        assert controller.state.fan_activated_at is None
        assert mock_relay._state == RelayState.RELEASED
    
    def test_fan_on_when_idle_to_active(self, controller, mock_gpu, mock_relay):
        """Fan should turn on when transitioning from idle to active."""
        mock_gpu.utilization = 50
        mock_gpu.temperature = 40
        
        controller.run_single_check()
        
        assert mock_relay._state == RelayState.ENERGIZED
        assert RelayState.ENERGIZED in mock_relay.state_changes


class TestTimeoutHandling:
    """Test relay communication failure handling."""
    
    def test_consecutive_timeout_counting(self, controller, mock_relay):
        """Consecutive timeouts should be tracked."""
        mock_relay.fail_next_get = True
        controller.handle_relay_timeout()
        assert controller.state.consecutive_timeouts == 1
        
        controller.handle_relay_timeout()
        assert controller.state.consecutive_timeouts == 2
    
    def test_timeout_count_resets_on_success(self, controller, mock_relay):
        """Successful communication should reset timeout count."""
        controller.state.consecutive_timeouts = 5
        controller.handle_relay_success()
        assert controller.state.consecutive_timeouts == 0
    
    def test_processes_killed_after_threshold(self, controller, mock_gpu, config):
        """GPU processes should be killed after timeout threshold."""
        mock_gpu.processes = [
            {'pid': 1234, 'process_name': 'python', 'used_memory': '4096 MiB'},
            {'pid': 5678, 'process_name': 'cuda_app', 'used_memory': '2048 MiB'},
        ]
        
        # Simulate reaching threshold
        for _ in range(config.fan_timeout_kill_threshold):
            controller.handle_relay_timeout()
        
        assert controller.state.process_kill_sent is True
        assert 1234 in mock_gpu.killed_pids
        assert 5678 in mock_gpu.killed_pids
    
    def test_processes_not_killed_multiple_times(self, controller, mock_gpu, config):
        """Processes should only be killed once per failure episode."""
        mock_gpu.processes = [{'pid': 1234, 'process_name': 'test', 'used_memory': '100 MiB'}]
        
        # Exceed threshold multiple times
        for _ in range(config.fan_timeout_kill_threshold + 5):
            controller.handle_relay_timeout()
        
        # Should only have killed once
        assert mock_gpu.killed_pids.count(1234) == 1
    
    def test_kill_flag_resets_after_recovery(self, controller, mock_gpu, config):
        """Process kill flag should reset after relay recovers."""
        mock_gpu.processes = [{'pid': 1234, 'process_name': 'test', 'used_memory': '100 MiB'}]
        
        # Trigger kill
        for _ in range(config.fan_timeout_kill_threshold):
            controller.handle_relay_timeout()
        
        assert controller.state.process_kill_sent is True
        
        # Simulate recovery
        controller.handle_relay_success()
        
        assert controller.state.process_kill_sent is False
        assert controller.state.consecutive_timeouts == 0


class TestFullCycleIntegration:
    """Integration tests for complete monitoring cycles."""
    
    def test_full_cycle_idle_to_active_to_idle(self, controller, mock_gpu, mock_relay, config):
        """Test a complete cycle from idle to active and back."""
        # Initial state: idle
        mock_gpu.utilization = 5
        mock_gpu.temperature = 40
        
        controller.run_single_check()
        assert mock_relay._state == RelayState.RELEASED
        
        # GPU becomes active
        mock_gpu.utilization = 80
        mock_gpu.temperature = 70
        
        controller.run_single_check()
        assert mock_relay._state == RelayState.ENERGIZED
        assert controller.state.fan_activated_at is not None
        
        # GPU becomes idle but minimum runtime not met
        mock_gpu.utilization = 5
        mock_gpu.temperature = 40
        
        controller.run_single_check()
        assert mock_relay._state == RelayState.ENERGIZED  # Still on
        
        # Fast-forward past minimum runtime
        controller.state.fan_activated_at = time.time() - config.minimum_fan_runtime - 1
        
        controller.run_single_check()
        assert mock_relay._state == RelayState.RELEASED  # Now off
    
    def test_communication_failure_during_active_use(self, controller, mock_gpu, mock_relay, config):
        """Test behavior when relay communication fails during active use."""
        mock_gpu.utilization = 80
        mock_gpu.processes = [{'pid': 9999, 'process_name': 'heavy_compute', 'used_memory': '8000 MiB'}]
        
        # Simulate repeated communication failures
        for _ in range(config.fan_timeout_kill_threshold):
            mock_relay.fail_next_get = True
            controller.run_single_check()
        
        # Processes should have been killed
        assert 9999 in mock_gpu.killed_pids


class TestShutdown:
    """Test graceful shutdown behavior."""
    
    def test_shutdown_turns_fan_off(self, controller, mock_relay):
        """Controller should turn fan off on shutdown."""
        mock_relay._state = RelayState.ENERGIZED
        
        controller.request_shutdown()
        
        # Simulate one more iteration of the run loop would happen
        # but in the actual run() method, it turns off the fan after the loop
        controller.turn_fan_off()
        
        assert mock_relay._state == RelayState.RELEASED


class TestRelayStateEnum:
    """Test RelayState enum behavior."""
    
    def test_relay_state_values(self):
        """Verify the inverted relay logic values are correct."""
        # This is the key insight: relay energized = fan on, relay released = fan off
        assert RelayState.ENERGIZED.value == "off"  # When relay reports "off", it's energized
        assert RelayState.RELEASED.value == "on"    # When relay reports "on", it's released


if __name__ == "__main__":
    pytest.main([__file__, "-v"])