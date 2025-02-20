"""Class to represent dali lamps."""
import json
import logging

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
        _min_physical_level = await self.driver.send(gear.QueryPhysicalMinimum(self.short_address))

        try:
            self.min_physical_level = _min_physical_level.value
        except Exception as err:
            self.min_physical_level = None
            logger.warning("Set min_physical_level to None as %s failed: %s", _min_physical_level, err)
        self.min_level = (await self.driver.send(gear.QueryMinLevel(self.short_address))).value
        self.max_level = (await self.driver.send(gear.QueryMaxLevel(self.short_address))).value
        self.__level = (await self.driver.send(gear.QueryActualLevel(self.short_address))).value
        
        self.tc_coolest = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTcCoolest))
        self.tc_warmest = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTcWarmest))
        self.__tc  = await self.driver.run_sequence(QueryDT8ColourValue(address=self.short_address, query=QueryColourValueDTR.ColourTemperatureTC))

    def gen_ha_config(self, mqtt_base_topic):
        """Generate a automatic configuration for Home Assistant."""
        json_config = {
            "name": self.friendly_name,
            "obj_id": f"dali_light_{self.device_name}",
            "uniq_id": f"{type(self.driver).__name__}_{self.short_address}",
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
            "sup_clrm": ["color_temp"],
            "clr_temp_stat_t": MQTT_COLOR_TEMP_STATE_TOPIC.format(
                mqtt_base_topic, self.device_name
            ),
            "clr_temp_cmd_t": MQTT_COLOR_TEMP_COMMAND_TOPIC.format(
                mqtt_base_topic, self.device_name
            ),
            "max_mirs": self.tc_warmest,
            "min_mirs": self.tc_coolest,
            "device": {
                "ids": "dali2mqtt",
                "name": "DALI Lights",
                "sw": f"dali2mqtt {__version__}",
                "mdl": f"{type(self.driver).__name__}",
                "mf": "dali2mqtt",
            },
        }
        return json.dumps(json_config)

    async def get_level(self):
        """Retrieve actual level from ballast."""
        self.__level = await self.driver.send(gear.QueryActualLevel(self.short_address))
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
        return (
            f"{self.device_name} - address: {self.short_address.address}, "
            f"actual brightness level: {self.level} (minimum: {self.min_level}, "
            f"max: {self.max_level}, physical minimum: {self.min_physical_level})"
        )
