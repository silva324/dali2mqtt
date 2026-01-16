"""Class to represent dali lamps."""
import json
import asyncio
import logging

import dali.address as address
import dali.gear.general as gear
from dali.gear.sequences import SetDT8ColourValueTc, SetDT8TcLimit, QueryDT8ColourValue
from dali.gear.colour import (
    tc_kelvin_mirek, 
    QueryColourValueDTR, 
    QueryColourStatus, 
    StoreColourTemperatureTcLimitDTR2,
)

from dali.sequences import QueryDeviceTypes
from dali.memory import info as mem_info, oem as mem_oem

from dali2mqtt.consts import (
    ALL_SUPPORTED_LOG_LEVELS,
    LOG_FORMAT,
    MQTT_AVAILABLE,
    MQTT_BRIGHTNESS_COMMAND_TOPIC,
    MQTT_BRIGHTNESS_STATE_TOPIC,
    MQTT_COLOR_TEMP_COMMAND_TOPIC,
    MQTT_COLOR_TEMP_STATE_TOPIC,
    MQTT_FADE_TIME_STATE_TOPIC,
    MQTT_FADE_TIME_COMMAND_TOPIC,
    MQTT_FADE_RATE_STATE_TOPIC,
    MQTT_FADE_RATE_COMMAND_TOPIC,
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

        if hasattr(self.short_address, 'address'):
            self.device_name = str(self.short_address.address)
        elif hasattr(self.short_address, 'group'):
            self.device_name = f"group_{self.short_address.group}"
        elif str(type(self.short_address).__name__) == 'Broadcast':
            self.device_name = "broadcast"
        else:
            self.device_name = slugify(friendly_name)

        self.device_type = "Generic DALI Ballast"
        self.gtin = "N/A"
        self.firmware_version = "N/A"
        self.luminaire_id = "N/A"
        logger.setLevel(ALL_SUPPORTED_LOG_LEVELS[log_level])

    async def init(self):
        """Initialize the lamp by querying its status and capabilities."""
        # Broadcast addresses cannot be queried, only commanded
        if isinstance(self.short_address, address.Broadcast):
            logger.info("Broadcast address detected, using default values (queries not supported)")
            self._initialize_default_values()
            return
        
        await self._initialize_limits()
        await self._initialize_fade_settings()
        await self._get_actual_level()
        await self._initialize_color_temperature()
        await self._initialize_device_type()
        await self._initialize_memory_bank_info()

    def _initialize_default_values(self):
        """Set default values when queries are not possible."""
        self.min_physical_level = 0
        self.min_level = 0
        self.max_level = 254
        self.__level = 0
        self.tc_coolest = None
        self.tc_warmest = None
        self.__tc = None
        self.fade_time = 0
        self.fade_rate = 0

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

    async def _initialize_fade_settings(self):
        """Query fade time and fade rate."""
        try:
           response = await self.driver.send(gear.QueryFadeTimeFadeRate(self.short_address))
           # The response object has .fade_time and .fade_rate properties
           # But they return integers from 1-15 (encoded).
           # We need to access the byte values from the response if possible, 
           # or trust the library implementation.
           # Checking library Source: QueryFadeTimeAndRateResponse has props that return value slices.
           # Let's check what they return.
           # Assuming they return integers.
           self.fade_time = int(response.fade_time)
           self.fade_rate = int(response.fade_rate)
           logger.debug("Lamp %s initialized with Fade Time: %s, Fade Rate: %s", 
                        self.friendly_name, self.fade_time, self.fade_rate)
        except Exception as err:
            logger.warning("Failed to query fade settings for %s: %s", self.friendly_name, err)
            self.fade_time = 0
            self.fade_rate = 0

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

    async def _initialize_device_type(self):
        """Query device type."""
        # Skip for groups/broadcast
        if hasattr(self.short_address, 'group') or isinstance(self.short_address, address.Broadcast):
            return

        try:
             # Use sequence to get list of types (handles 'multiple' response)
             type_ids = await self.driver.run_sequence(QueryDeviceTypes(self.short_address))
             
             if type_ids:
                 types_str = []
                 for tid in type_ids:
                     if tid in gear.QueryDeviceTypeResponse._types:
                         types_str.append(gear.QueryDeviceTypeResponse._types[tid])
                     else:
                         types_str.append(str(tid))
                 self.device_type = ", ".join(types_str)

        except Exception as err:
             logger.debug("Failed to query device type for %s: %s", self.friendly_name, err)

    async def _initialize_memory_bank_info(self):
        """Query memory bank info (GTIN, FW, Model)."""
        if hasattr(self.short_address, 'group') or isinstance(self.short_address, address.Broadcast):
            return

        try:
             # Try to read Luminaire ID from Bank 1 (OEM)
             self.luminaire_id = await self.driver.run_sequence(mem_oem.LuminaireIdentification.read(self.short_address))
             if not isinstance(self.luminaire_id, str) or not self.luminaire_id.strip():
                 self.luminaire_id = None
             else:
                 self.luminaire_id = self.luminaire_id.strip()
        except Exception as err:
             logger.debug("Failed to read Luminaire ID for %s: %s", self.friendly_name, err)
             self.luminaire_id = None
        
        try:
            # Try to read GTIN from Bank 0
            gtin_val = await self.driver.run_sequence(mem_info.GTIN.read(self.short_address))
            if isinstance(gtin_val, int):
                self.gtin = str(gtin_val)
            else:
                self.gtin = None
        except Exception as err:
             logger.debug("Failed to read GTIN for %s: %s", self.friendly_name, err)
             self.gtin = None

        try:
            # Try to read FW Version from Bank 0
            self.firmware_version = await self.driver.run_sequence(mem_info.FirmwareVersion.read(self.short_address))
        except Exception as err:
            logger.debug("Failed to read Lamp FW for %s: %s", self.friendly_name, err)
            self.firmware_version = None

    def _get_sw_version(self):
        """Generate software version string including driver info."""
        driver_name = type(self.driver).__name__
        driver_fw = getattr(self.driver, "firmware_version", None)
        
        sw_version = f"dali2mqtt {__version__}"
        if driver_name:
             # Clean up common driver names for display
             if "Hasseb" in driver_name: driver_name = "Hasseb"
             elif "Tridonic" in driver_name: driver_name = "Tridonic"
             
             sw_version += f" / {driver_name}"
             if driver_fw:
                 sw_version += f" {driver_fw}"
        
        # Lamp FW Info
        if self.firmware_version and self.firmware_version != "not implemented":
            sw_version += f" / Lamp FW {self.firmware_version}"

        return sw_version

    def gen_ha_config(self, mqtt_base_topic):
        """Generate a automatic configuration for Home Assistant."""
        # Generate proper unique ID for lamps, groups, and broadcast
        if hasattr(self.short_address, 'address'):
            unique_id = f"dali2mqtt_lamp_{self.short_address.address}"
        elif hasattr(self.short_address, 'group'):
            unique_id = f"dali2mqtt_group_{self.short_address.group}"
        elif str(type(self.short_address).__name__) == 'Broadcast':
            unique_id = f"dali2mqtt_broadcast"
        else:
            unique_id = f"dali2mqtt_{self.device_name}"
        
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
                "ids": unique_id,
                "name": self.friendly_name,
                "sw": self._get_sw_version(),
                "mdl": self.luminaire_id if self.luminaire_id else self.device_type,
                "hw": self.gtin if self.gtin else "rev 1.0",
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

    def gen_ha_config_fade_time(self, mqtt_base_topic):
        """Generate HA config for Fade Time."""
        if hasattr(self.short_address, 'address'):
            base_unique_id = f"dali2mqtt_lamp_{self.short_address.address}"
        elif hasattr(self.short_address, 'group'):
            base_unique_id = f"dali2mqtt_group_{self.short_address.group}"
        elif str(type(self.short_address).__name__) == 'Broadcast':
            base_unique_id = f"dali2mqtt_broadcast"
        else:
            base_unique_id = f"dali2mqtt_{self.device_name}"
            
        unique_id = f"{base_unique_id}_fadetime"

        json_config = {
            "name": "Fade Time",
            "uniq_id": unique_id,
            "stat_t": MQTT_FADE_TIME_STATE_TOPIC.format(mqtt_base_topic, self.device_name),
            "cmd_t": MQTT_FADE_TIME_COMMAND_TOPIC.format(mqtt_base_topic, self.device_name),
            "min": 0,
            "max": 15,
            "mode": "box",
            "entity_category": "config",
            "avty_t": MQTT_DALI2MQTT_STATUS.format(mqtt_base_topic),
            "pl_avail": MQTT_AVAILABLE,
            "pl_not_avail": MQTT_NOT_AVAILABLE,
            "icon": "mdi:timer-sand",
             "device": {
                "ids": base_unique_id,
                "name": self.friendly_name,
                "sw": self._get_sw_version(),
                "mdl": self.luminaire_id if self.luminaire_id else self.device_type,
                "hw": self.gtin if self.gtin else "rev 1.0",
                "mf": "dali2mqtt",
            },
        }
        return json.dumps(json_config)

    def gen_ha_config_fade_rate(self, mqtt_base_topic):
        """Generate HA config for Fade Rate."""
        if hasattr(self.short_address, 'address'):
            base_unique_id = f"dali2mqtt_lamp_{self.short_address.address}"
        elif hasattr(self.short_address, 'group'):
            base_unique_id = f"dali2mqtt_group_{self.short_address.group}"
        elif str(type(self.short_address).__name__) == 'Broadcast':
            base_unique_id = f"dali2mqtt_broadcast"
        else:
            base_unique_id = f"dali2mqtt_{self.device_name}"
        
        unique_id = f"{base_unique_id}_faderate"

        json_config = {
            "name": "Fade Rate",
            "uniq_id": unique_id,
            "stat_t": MQTT_FADE_RATE_STATE_TOPIC.format(mqtt_base_topic, self.device_name),
            "cmd_t": MQTT_FADE_RATE_COMMAND_TOPIC.format(mqtt_base_topic, self.device_name),
            "min": 1,
            "max": 15,
            "mode": "box",
            "entity_category": "config",
            "avty_t": MQTT_DALI2MQTT_STATUS.format(mqtt_base_topic),
            "pl_avail": MQTT_AVAILABLE,
            "pl_not_avail": MQTT_NOT_AVAILABLE,
            "icon": "mdi:speedometer",
             "device": {
                "ids": base_unique_id,
                "name": self.friendly_name,
                "sw": self._get_sw_version(),
                "mdl": self.luminaire_id if self.luminaire_id else self.device_type,
                "hw": self.gtin if self.gtin else "rev 1.0",
                "mf": "dali2mqtt",
            },
        }
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
        
    async def set_fade_time(self, value):
        """Set fade time configuration."""
        if not 0 <= value <= 15:
             raise ValueError("Fade time must be between 0 and 15")
        
        # Prepare DTR0 with the value
        await self.driver.send(gear.DTR0(value))
        # Send Store DTR as Fade Time command
        # Must be sent twice
        await self.driver.send(gear.SetFadeTime(self.short_address))
        await self.driver.send(gear.SetFadeTime(self.short_address))
        
        self.fade_time = value
        logger.debug("Set lamp <%s> fade time to %s", self.friendly_name, self.fade_time)

    async def set_fade_rate(self, value):
        """Set fade rate configuration."""
        if not 1 <= value <= 15:
             raise ValueError("Fade rate must be between 1 and 15")
             
        # Prepare DTR0 with the value
        await self.driver.send(gear.DTR0(value))
        # Send Store DTR as Fade Rate command
        # Must be sent twice
        await self.driver.send(gear.SetFadeRate(self.short_address))
        await self.driver.send(gear.SetFadeRate(self.short_address))
        
        self.fade_rate = value
        logger.debug("Set lamp <%s> fade rate to %s", self.friendly_name, self.fade_rate)

    async def set_tc(self, value):
        """Commit level to ballast."""
        if self.tc_coolest is None or self.tc_warmest is None:
             raise ValueError("Color temperature not supported")

        if not self.tc_coolest <= value <= self.tc_warmest:
            raise ValueError(f"Value {value} out of range ({self.tc_coolest}-{self.tc_warmest})")
            
        self.__tc = value
        
        await self.driver.run_sequence(SetDT8ColourValueTc(address=self.short_address, tc_mired=value))
        
        logger.debug(
            "Set lamp <%s> color temp to %s", self.friendly_name, self.__tc
        )

    def set_level_local(self, value):
        """Set cached level without sending DALI commands."""
        self.__level = value

    def set_tc_local(self, value):
        """Set cached color temp without sending DALI commands."""
        self.__tc = value

    async def off(self):
        """Turn off ballast."""
        await self.driver.send(gear.Off(self.short_address))
        self.__level = 0

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
