"""Class to represent dali lamps."""
import json
import asyncio
import logging

import dali.address as address
import dali.gear.general as gear
from dali.gear.sequences import SetDT8ColourValueTc, SetDT8TcLimit, QueryDT8ColourValue
from dali.gear.colour import tc_kelvin_mirek, QueryColourValueDTR, QueryColourStatus, StoreColourTemperatureTcLimitDTR2

from dali2mqtt.consts import (
    ALL_SUPPORTED_LOG_LEVELS,
    LOG_FORMAT,
    MQTT_AVAILABLE,
    MQTT_BRIGHTNESS_COMMAND_TOPIC,
    MQTT_BRIGHTNESS_STATE_TOPIC,
    MQTT_COLOR_TEMP_COMMAND_TOPIC,
    MQTT_COLOR_TEMP_STATE_TOPIC,
    MQTT_COMMAND_TOPIC,
    MQTT_DALI2MQTT_STATUS,
    MQTT_NOT_AVAILABLE,
    MQTT_PAYLOAD_OFF,
    MQTT_STATE_TOPIC,
    __version__,
)
from slugify import slugify

logging.basicConfig(format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class Lamp:
    """Representation of a DALI Lamp."""

    def __init__(
        self,
        log_level,
        driver,
        friendly_name,
        short_address,
    ):
        """Initialize Lamp."""
        self.driver = driver
        self.short_address = short_address
        self.friendly_name = friendly_name

        self.device_name = slugify(friendly_name)

        logger.setLevel(ALL_SUPPORTED_LOG_LEVELS[log_level])

    async def init(self):
        """Initialize the lamp by querying its status and capabilities."""
        # Broadcast addresses cannot be queried, only commanded
        if isinstance(self.short_address, address.Broadcast):
            logger.info("Broadcast address detected, using default values (queries not supported)")
            self._initialize_default_values()
            return
        
        await self._initialize_limits()
        await self._get_actual_level()
        await self._initialize_color_temperature()

    def _initialize_default_values(self):
        """Set default values when queries are not possible."""
        self.min_physical_level = 0
        self.min_level = 0
        self.max_level = 254
        self.__level = 0
        self.tc_coolest = None
        self.tc_warmest = None
        self.__tc = None

    async def _initialize_limits(self):
        """Query physical minimum, min level, and max level."""
        # Query physical minimum
        _min_physical_level = await self.driver.send(gear.QueryPhysicalMinimum(self.short_address))
        try:
            self.min_physical_level = int(_min_physical_level.value)
        except (ValueError, TypeError):
            self.min_physical_level = 0
            logger.warning("Set min_physical_level to 0 as %s returned non-numeric value", _min_physical_level)
        
        # Query min level
        try:
            min_level_response = await self.driver.send(gear.QueryMinLevel(self.short_address))
            self.min_level = int(min_level_response.value)
        except (ValueError, TypeError):
            self.min_level = 0
            logger.warning("Set min_level to 0 due to non-numeric response")
            
        # Query max level
        try:
            max_level_response = await self.driver.send(gear.QueryMaxLevel(self.short_address))
            self.max_level = int(max_level_response.value)
        except (ValueError, TypeError):
            self.max_level = 254
            logger.warning("Set max_level to 254 due to non-numeric response")

    async def _get_actual_level(self):
        """Determine if lamp is ON and query its actual level with retries."""
        try:
            # Bit 2 of status byte indicates 'Lamp On'
            status_resp = await self.driver.send(gear.QueryStatus(self.short_address))
            status_byte = status_resp.value.as_integer
            lamp_on = (status_byte & 0x04) != 0
            fade_running = (status_byte & 0x10) != 0
            address_str = getattr(self.short_address, 'address', getattr(self.short_address, 'group', self.short_address))
            
            if not lamp_on:
                logger.debug("Lamp %s is OFF (Status Bit 2 is 0)", address_str)
                self.__level = 0
                return

            # Lamp is ON, query actual level
            await self._query_actual_level_with_retry(address_str, fade_running)

        except Exception as err:
            logger.warning("Failed to query status/level for %s: %s", getattr(self.short_address, 'address', self.short_address), err)
            self.__level = 0

    async def _query_actual_level_with_retry(self, address_str, fade_running):
        """Query actual level with retry logic for MASK responses."""
        max_retries = 3
        retry_delays = [0.2, 0.4, 0.8]
        
        # If fade is running, might get MASK, so wait a bit first
        if fade_running:
             logger.debug("Lamp %s Fade Running (Status Bit 4), waiting...", address_str)
             await asyncio.sleep(0.5)

        for attempt in range(max_retries):
            level_response = (await self.driver.send(gear.QueryActualLevel(self.short_address))).value
            try:
                self.__level = int(level_response)
                return
            except (ValueError, TypeError):
                if str(level_response) == 'MASK':
                    if attempt < max_retries - 1:
                        logger.debug("Lamp %s returned MASK (attempt %d), retrying...", address_str, attempt+1)
                        await asyncio.sleep(retry_delays[attempt])
                    else:
                        logger.warning("Lamp %s MASK persists. Status says ON, defaulting to 254", address_str)
                        self.__level = 254 # Assume full brightness if we know it's ON but can't read level
                else:
                    self.__level = 254 # Fallback for non-numeric if ON
                    logger.warning("Lamp %s invalid level '%s' but Lamp is ON, defaulting to 254", address_str, level_response)
                    return

    async def _initialize_color_temperature(self):
        """Query color temperature capabilities (DT8)."""
        # Add small delay after level query for device stability
        await asyncio.sleep(0.1)
        
        try:
            tc_coolest_response = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTcCoolest))
            tc_warmest_response = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTcWarmest))
            tc_response = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTC))
            
            # Extract values and check if color temp is supported
            self.tc_coolest = int(tc_coolest_response) if tc_coolest_response is not None else None
            self.tc_warmest = int(tc_warmest_response) if tc_warmest_response is not None else None
            self.__tc = int(tc_response) if tc_response is not None else None
            
            # If any value is invalid, disable color temp support
            if self.tc_coolest is None or self.tc_warmest is None or self.tc_coolest == 0 or self.tc_warmest == 0:
                self.tc_coolest = None
                self.tc_warmest = None
                self.__tc = None
            else:
                logger.debug("Lamp %s supports color temp: %s-%s mired, current: %s", 
                           getattr(self.short_address, 'address', self.short_address),
                           self.tc_coolest, self.tc_warmest, self.__tc)
        except Exception as err:
            logger.debug("Lamp %s doesn't support color temperature: %s", getattr(self.short_address, 'address', self.short_address), err)
            self.tc_coolest = None
            self.tc_warmest = None
            self.__tc = None

    def gen_ha_config(self, mqtt_base_topic):
        """Generate a automatic configuration for Home Assistant."""
        # Generate proper unique ID for lamps, groups, and broadcast
        if hasattr(self.short_address, 'address'):
            unique_id = f"{type(self.driver).__name__}_lamp_{self.short_address.address}"
        elif hasattr(self.short_address, 'group'):
            unique_id = f"{type(self.driver).__name__}_group_{self.short_address.group}"
        elif str(type(self.short_address).__name__) == 'Broadcast':
            unique_id = f"{type(self.driver).__name__}_broadcast"
        else:
            unique_id = f"{type(self.driver).__name__}_{self.device_name}"
        
        json_config = {
            "name": self.friendly_name,
            "def_ent_id": f"dali_light_{self.device_name}",
            "uniq_id": unique_id,
            "stat_t": MQTT_STATE_TOPIC.format(mqtt_base_topic, self.device_name),
            "cmd_t": MQTT_COMMAND_TOPIC.format(mqtt_base_topic, self.device_name),
            "pl_off": MQTT_PAYLOAD_OFF.decode("utf-8"),
            "bri_stat_t": MQTT_BRIGHTNESS_STATE_TOPIC.format(
                mqtt_base_topic, self.device_name
            ),
            "bri_cmd_t": MQTT_BRIGHTNESS_COMMAND_TOPIC.format(
                mqtt_base_topic, self.device_name
            ),
            "bri_scl": self.max_level,
            "on_cmd_type": "brightness",
            "avty_t": MQTT_DALI2MQTT_STATUS.format(mqtt_base_topic),
            "pl_avail": MQTT_AVAILABLE,
            "pl_not_avail": MQTT_NOT_AVAILABLE,
            "brightness": "true",
            "device": {
                "ids": "dali2mqtt",
                "name": "DALI Lights",
                "sw": f"dali2mqtt {__version__}",
                "mdl": f"{type(self.driver).__name__}",
                "mf": "dali2mqtt",
            },
        }
        
        # Add color temperature support only if lamp supports it
        if self.tc_coolest is not None and self.tc_warmest is not None:
            json_config["sup_clrm"] = ["color_temp"]
            json_config["clr_temp_stat_t"] = MQTT_COLOR_TEMP_STATE_TOPIC.format(mqtt_base_topic, self.device_name)
            json_config["clr_temp_cmd_t"] = MQTT_COLOR_TEMP_COMMAND_TOPIC.format(mqtt_base_topic, self.device_name)
            json_config["max_mirs"] = self.tc_warmest
            json_config["min_mirs"] = self.tc_coolest
        
        return json.dumps(json_config)

    async def get_level(self):
        """Retrieve actual level from ballast."""
        import asyncio
        max_retries = 2
        
        for attempt in range(max_retries):
            response = await self.driver.send(gear.QueryActualLevel(self.short_address))
            try:
                self.__level = int(response.value)
                return self.__level
            except (ValueError, TypeError):
                address_str = getattr(self.short_address, 'address', getattr(self.short_address, 'group', self.short_address))
                if str(response.value) == 'MASK':
                    if attempt < max_retries - 1:
                        logger.debug("Lamp %s returned MASK, retrying...", address_str)
                        await asyncio.sleep(0.2)
                    else:
                        logger.debug("Lamp %s still in transition after retries, using cached value", address_str)
                        # Keep existing __level value
                else:
                    logger.warning("Lamp %s returned non-numeric level: %s, keeping cached value", address_str, response.value)
                    break
        
        return self.__level

    @property
    def level(self):
        """Return brightness level."""
        # logger.debug(
        #     "Get lamp <%s> brightness level %s", self.friendly_name, self.__level
        # )
        return self.__level
    
    @property
    def tc(self):
        """Return Color Temperature in mired."""
        return self.__tc

    # @level.setter
    async def set_level(self, value):
        """Commit level to ballast."""
        value_scaled  = int(self.min_level + (value) * ((self.max_level - self.min_level) / (254)))
        if not self.min_level <= value_scaled <= self.max_level and value != 0:
            raise ValueError
        self.__level = value
        await self.driver.send(gear.DAPC(self.short_address, value_scaled))
        logger.debug(
            "Set lamp <%s> brightness level to %s, actual: %s", self.friendly_name, self.__level, value_scaled
        )
        
    async def get_tc(self):
        """Retrieve actual level from ballast."""
        self.__tc  = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTC))
        return self.__tc
        
    async def set_tc(self, value):
        """Commit level to ballast."""
        # value_scaled  = int(self.min_level + (value) * ((self.max_level - self.min_level) / (254)))
        if not self.tc_coolest <= value <= self.tc_warmest and value != 0:
            raise ValueError
        self.__tc = value
        await self.driver.run_sequence(SetDT8ColourValueTc(address=self.short_address, tc_mired=value))
        logger.debug(
            "Set lamp <%s> color temp to %s", self.friendly_name, self.__tc
        )

    async def off(self):
        """Turn off ballast."""
        await self.driver.send(gear.Off(self.short_address))

    def __str__(self):
        """Serialize lamp information."""
        # Handle GearShort (has .address), GearGroup (has .group), and Broadcast addresses
        if hasattr(self.short_address, 'address'):
            address_value = self.short_address.address
        elif hasattr(self.short_address, 'group'):
            address_value = self.short_address.group
        elif str(type(self.short_address).__name__) == 'Broadcast':
            address_value = 'broadcast'
        else:
            address_value = str(self.short_address)
        
        return (
            f"{self.device_name} - address: {address_value}, "
            f"actual brightness level: {self.level} (minimum: {self.min_level}, "
            f"max: {self.max_level}, physical minimum: {self.min_physical_level})"
        )
