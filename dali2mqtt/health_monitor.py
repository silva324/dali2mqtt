"""Health monitoring for dali2mqtt bridge."""

import logging
import asyncio
import time
from enum import Enum

logger = logging.getLogger(__name__)


class BridgeStatus(Enum):
    """Possible bridge statuses."""
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    RECONNECTING = "reconnecting"


class HealthMonitor:
    """Monitor system health and detect issues."""

    def __init__(self, check_interval=30):
        """Initialize health monitor.
        
        Args:
            check_interval: Seconds between health checks (default 30)
        """
        self.check_interval = check_interval
        self.last_successful_dali_command = time.time()
        self.last_successful_mqtt_publish = time.time()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.status = BridgeStatus.ONLINE
        self._monitoring = False

    def record_dali_command_success(self):
        """Record successful DALI command."""
        self.last_successful_dali_command = time.time()
        self.consecutive_failures = 0
        if self.status != BridgeStatus.ONLINE:
            logger.info("DALI connectivity recovered")
            self.status = BridgeStatus.ONLINE

    def record_dali_command_failure(self):
        """Record failed DALI command."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            logger.warning(
                "DALI communication failed %d times consecutively",
                self.consecutive_failures
            )
            if self.status == BridgeStatus.ONLINE:
                self.status = BridgeStatus.DEGRADED

    def record_mqtt_publish_success(self):
        """Record successful MQTT publish."""
        self.last_successful_mqtt_publish = time.time()

    def record_mqtt_publish_failure(self):
        """Record failed MQTT publish."""
        logger.warning("MQTT publish failed")
        self.status = BridgeStatus.DEGRADED

    def get_status_summary(self):
        """Get current health status summary."""
        current_time = time.time()
        dali_timeout = current_time - self.last_successful_dali_command > (
            self.check_interval * 2
        )
        mqtt_timeout = current_time - self.last_successful_mqtt_publish > (
            self.check_interval * 2
        )

        issues = []
        if dali_timeout:
            issues.append("DALI: No successful commands in last {}s".format(
                int(current_time - self.last_successful_dali_command)
            ))
        if mqtt_timeout:
            issues.append("MQTT: No successful publishes in last {}s".format(
                int(current_time - self.last_successful_mqtt_publish)
            ))

        return {
            "status": self.status.value,
            "dali_ok": not dali_timeout,
            "mqtt_ok": not mqtt_timeout,
            "consecutive_failures": self.consecutive_failures,
            "issues": issues if issues else ["System operational"],
            "time_since_dali": int(current_time - self.last_successful_dali_command),
            "time_since_mqtt": int(current_time - self.last_successful_mqtt_publish),
        }

    async def start_monitoring(self, callback):
        """Start periodic health monitoring.
        
        Args:
            callback: Async function to call when status changes
        """
        self._monitoring = True
        previous_status = self.status
        
        while self._monitoring:
            try:
                await asyncio.sleep(self.check_interval)
                
                if self.status != previous_status:
                    logger.warning(
                        "Bridge status changed: %s -> %s",
                        previous_status.value,
                        self.status.value
                    )
                    try:
                        await callback(self.status)
                    except Exception as e:
                        logger.error("Error in status change callback: %s", e)
                    previous_status = self.status
                    
            except Exception as e:
                logger.error("Error in health monitoring: %s", e)

    def stop_monitoring(self):
        """Stop health monitoring."""
        self._monitoring = False
