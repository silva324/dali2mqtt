"""Driver manager for DALI USB device reconnection."""

import logging
import asyncio
import time
from dali.exceptions import DALIError

logger = logging.getLogger(__name__)


class DriverManager:
    """Manage DALI driver lifecycle with automatic reconnection."""

    def __init__(self, driver, driver_type, max_reconnect_attempts=5, health_monitor=None):
        """Initialize driver manager.
        
        Args:
            driver: DALI driver instance
            driver_type: Type of driver (HID_HASSEB, HID_TRIDONIC, etc.)
            max_reconnect_attempts: Max reconnection attempts before giving up
            health_monitor: Optional HealthMonitor instance to report to
        """
        self.driver = driver
        self.driver_type = driver_type
        self.max_reconnect_attempts = max_reconnect_attempts
        self.health_monitor = health_monitor
        self.reconnect_attempt = 0
        self.last_reconnect_time = None
        self.is_connected = False
        self._check_connection_task = None

    async def ensure_connected(self, timeout=30):
        """Ensure driver is connected, attempt reconnection if needed.
        
        Args:
            timeout: Timeout for connection attempt
            
        Returns:
            True if connected, False otherwise
        """
        try:
            # Check if driver is currently connected
            if hasattr(self.driver, "connected"):
                # For HID drivers with .connected event
                try:
                    await asyncio.wait_for(
                        self.driver.connected.wait(),
                        timeout=1
                    )
                    self.is_connected = True
                    self.reconnect_attempt = 0
                    return True
                except asyncio.TimeoutError:
                    logger.warning("Driver connection timeout")
                    self.is_connected = False
                    return await self._attempt_reconnection()
            else:
                # Assume connected if no connection event
                self.is_connected = True
                return True
                
        except Exception as e:
            logger.error("Error checking connection: %s", e)
            self.is_connected = False
            return await self._attempt_reconnection()

    async def _attempt_reconnection(self):
        """Attempt to reconnect to the DALI device.
        
        Returns:
            True if reconnection successful, False otherwise
        """
        if self.reconnect_attempt >= self.max_reconnect_attempts:
            logger.error(
                "Max reconnection attempts (%d) reached",
                self.max_reconnect_attempts
            )
            return False

        self.reconnect_attempt += 1
        backoff_delay = min(2 ** self.reconnect_attempt, 30)  # Max 30s backoff
        
        logger.info(
            "Attempting to reconnect (attempt %d/%d) - waiting %ds",
            self.reconnect_attempt,
            self.max_reconnect_attempts,
            backoff_delay
        )
        
        await asyncio.sleep(backoff_delay)
        
        try:
            if hasattr(self.driver, "disconnect"):
                try:
                    self.driver.disconnect()
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.debug("Error disconnecting driver: %s", e)
            
            if hasattr(self.driver, "connect"):
                self.driver.connect()
                logger.info("Driver reconnection initiated")
                
                # Wait for connection to establish
                if hasattr(self.driver, "connected"):
                    try:
                        await asyncio.wait_for(
                            self.driver.connected.wait(),
                            timeout=10
                        )
                        logger.info("Driver successfully reconnected")
                        self.is_connected = True
                        self.reconnect_attempt = 0
                        return True
                    except asyncio.TimeoutError:
                        logger.warning("Driver reconnection timeout")
                        return False
                else:
                    # Assume success if no connection event
                    self.is_connected = True
                    self.reconnect_attempt = 0
                    return True
            else:
                logger.error("Driver does not support reconnection")
                return False
                
        except Exception as e:
            logger.error("Reconnection attempt failed: %s", e)
            return False

    async def send(self, command):
        """Send command to DALI bus, ensuring connection first.
        
        Args:
            command: DALI command to send
            
        Returns:
            Command response
        """
        try:
            await self.ensure_connected()
            result = await self.driver.send(command)
            if self.health_monitor:
                self.health_monitor.record_dali_command_success()
            return result
        except Exception:
            if self.health_monitor:
                self.health_monitor.record_dali_command_failure()
            raise

    async def run_sequence(self, sequence):
        """Run sequence on DALI bus, ensuring connection first.
        
        Args:
            sequence: DALI sequence to run
            
        Returns:
            Sequence result
        """
        try:
            await self.ensure_connected()
            result = await self.driver.run_sequence(sequence)
            if self.health_monitor:
                self.health_monitor.record_dali_command_success()
            return result
        except Exception:
            if self.health_monitor:
                self.health_monitor.record_dali_command_failure()
            raise

    def get_connection_status(self):
        """Get current connection status."""
        return {
            "connected": self.is_connected,
            "reconnect_attempts": self.reconnect_attempt,
            "max_attempts": self.max_reconnect_attempts,
            "can_retry": self.reconnect_attempt < self.max_reconnect_attempts,
            "driver_type": self.driver_type,
        }
