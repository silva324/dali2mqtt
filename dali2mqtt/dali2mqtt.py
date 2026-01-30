#!/usr/bin/env python3
"""Bridge between a DALI controller and an MQTT bus."""

import argparse
import logging
import random
import re
import time
import os
import asyncio
import json

import paho.mqtt.client as mqtt

import dali.address as address
import dali.gear.general as gear
import dali.frame
from dali.command import YesNoResponse
from dali.exceptions import DALIError

from dali2mqtt.devicesnamesconfig import DevicesNamesConfig
from dali2mqtt.lamp import Lamp
from dali2mqtt.config import Config
from dali2mqtt.health_monitor import HealthMonitor, BridgeStatus
from dali2mqtt.driver_manager import DriverManager
from dali2mqtt.consts import (
    ALL_SUPPORTED_LOG_LEVELS,
    CONF_CONFIG,
    CONF_DALI_DRIVER,
    CONF_DALI_LAMPS,
    CONF_DEVICES_NAMES_FILE,
    CONF_HA_DISCOVERY_PREFIX,
    CONF_LOG_COLOR,
    CONF_LOG_LEVEL,
    CONF_MQTT_BASE_TOPIC,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_SERVER,
    CONF_MQTT_USERNAME,
    DALI_DRIVERS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_HA_DISCOVERY_PREFIX,
    HA_DISCOVERY_PREFIX,
    HA_DISCOVERY_PREFIX_NUMBER,
    HA_DISCOVERY_PREFIX_SENSOR,
    HA_DISCOVERY_PREFIX_BINARY_SENSOR,
    HA_DISCOVERY_PREFIX_BUTTON,
    HA_DISCOVERY_PREFIX_SELECT,
    HID_HASSEB,
    HID_TRIDONIC,
    LOG_FORMAT,
    MAX_RETRIES,
    MIN_BACKOFF_TIME,
    MAX_BACKOFF_TIME,
    MIN_HASSEB_FIRMWARE_VERSION,
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
    MQTT_PAYLOAD_ON,
    MQTT_SCAN_LAMPS_COMMAND_TOPIC,
    MQTT_STATE_TOPIC,
    RED_COLOR,
    YELLOW_COLOR,
    __version__,
)


from slugify import slugify

logging.basicConfig(format=LOG_FORMAT, level=os.environ.get("LOGLEVEL", "INFO"))
logger = logging.getLogger(__name__)


async def dali_scan(dali_driver):
    """Scan a maximum number of dali devices."""
    lamps = []
    for lamp in range(0, 64):
        try:
            logging.debug("Search for Lamp %s", lamp)
            present = await dali_driver.send(
                gear.QueryControlGearPresent(address.Short(lamp))
            )
            if isinstance(present, YesNoResponse) and present.value:
                # Double check to avoid ghosts (common with old firmware or noise)
                try:
                     # Try to read physical minimum level as confirmation
                     await dali_driver.send(gear.QueryPhysicalMinimum(address.Short(lamp)))
                     lamps.append(lamp)
                     logger.debug("Found lamp at address %d", lamp)
                except DALIError:
                     logger.warning("Lamp at %d reported present but failed confirmation scan. Ignoring.", lamp)

        except DALIError as err:
            logger.debug("%s not present: %s", lamp, err)
    return lamps


async def scan_groups(dali_driver, lamps):
    """Scan for groups."""
    logger.info("Scanning for groups")
    groups = {}
    for lamp in lamps:
        try:
            logging.debug("Search for groups for Lamp {}".format(lamp))
            group1 = (await dali_driver.send(
                gear.QueryGroupsZeroToSeven(address.GearShort(lamp))
            )).value.as_integer
            group2 = (await dali_driver.send(
                gear.QueryGroupsEightToFifteen(address.GearShort(lamp))
            )).value.as_integer

            logger.debug("Group 0-7: %d", group1)
            logger.debug("Group 8-15: %d", group2)

            lamp_groups = []

            for i in range(8):
                checkgroup = 1 << i
                logging.debug("Check pattern: %d", checkgroup)
                if (group1 & checkgroup) == checkgroup:
                    if i not in groups:
                        groups[i] = []
                    groups[i].append(lamp)
                    lamp_groups.append(i)
                if (group2 & checkgroup) != 0:
                    if not i + 8 in groups:
                        groups[i + 8] = []
                    groups[i + 8].append(lamp)
                    lamp_groups.append(i + 8)

            logger.debug("Lamp %d is in groups %s", lamp, lamp_groups)

        except Exception as e:
            logger.warning("Can't get groups for lamp %s: %s", lamp, e)
    logger.info("Finished scanning for groups")
    return groups


async def initialize_bridge_device(mqtt_client, data_object):
    """Initialize the Bridge device in Home Assistant."""
    mqtt_base_topic = data_object["base_topic"]
    ha_prefix = data_object["ha_prefix"]
    # Get driver type safely
    driver = data_object.get("driver")
    driver_type = "unknown"
    if driver and hasattr(driver, "driver_type"):
        driver_type = driver.driver_type

    device_info = {
        "identifiers": ["dali2mqtt_bridge"],
        "name": "DALI2MQTT Bridge",
        "model": f"DALI Bridge ({driver_type})",
        "manufacturer": "dali2mqtt",
        "sw_version": __version__,
    }

    # 1. Bridge Status Sensor (Enum)
    status_config = {
        "name": "Bridge Status",
        "unique_id": f"dali2mqtt_bridge_status",
        "state_topic": f"{mqtt_base_topic}/bridge/status",
        "value_template": "{{ value_json.status }}",
        "icon": "mdi:bridge",
        "device": device_info,
        "entity_category": "diagnostic",
    }
    mqtt_client.publish(
        HA_DISCOVERY_PREFIX_SENSOR.format(ha_prefix, "dali2mqtt_bridge_status"),
        json.dumps(status_config),
        qos=1, retain=True
    )

    # 2. Bus Error Binary Sensor
    bus_error_config = {
        "name": "DALI Bus Error",
        "unique_id": f"dali2mqtt_bridge_bus_error",
        "state_topic": f"{mqtt_base_topic}/bridge/status",
        "value_template": "{{ 'ON' if value_json.bus_error else 'OFF' }}",
        "device_class": "problem",
        "device": device_info,
        "entity_category": "diagnostic",
    }
    mqtt_client.publish(
        HA_DISCOVERY_PREFIX_BINARY_SENSOR.format(ha_prefix, "dali2mqtt_bridge_bus_error"),
        json.dumps(bus_error_config),
        qos=1, retain=True
    )

    # 3. Restart Button
    restart_config = {
        "name": "Restart Bridge",
        "unique_id": f"dali2mqtt_bridge_restart",
        "command_topic": f"{mqtt_base_topic}/bridge/request/restart",
        "payload_press": "restart",
        "icon": "mdi:restart",
        "device": device_info,
        "entity_category": "config",
    }
    mqtt_client.publish(
        HA_DISCOVERY_PREFIX_BUTTON.format(ha_prefix, "dali2mqtt_bridge_restart"),
        json.dumps(restart_config),
        qos=1, retain=True
    )
    logger.info("Bridge device initialized")


async def initialize_lamps(data_object, client):
    """Initialize all lamps and groups."""
    
    # Initialize bridge device
    await initialize_bridge_device(client, data_object)

    driver = data_object["driver"]
    mqtt_base_topic = data_object["base_topic"]
    ha_prefix = data_object["ha_prefix"]
    log_level = data_object["log_level"]
    devices_names_config = data_object["devices_names_config"]
    devices_names_config.load_devices_names_file()
    lamps = await dali_scan(driver)
    logger.info(
        "Found %d lamps",
        len(lamps),
    )

    async def create_mqtt_lamp(address, name):
        try:
            lamp_object = Lamp(
                log_level,
                driver,
                name,
                address,
            )
            await lamp_object.init()

            # Store lamp using device_name (slugified) to match MQTT topic lookups
            data_object["all_lamps"][lamp_object.device_name] = lamp_object

            # Cleanup old discovery topic if necessary
            # Old version used slugify(friendly_name) as the ID in the topic.
            # New version uses device_name (address).
            old_name_slug = slugify(name)
            if old_name_slug != lamp_object.device_name:
                 old_topic = HA_DISCOVERY_PREFIX.format(ha_prefix, old_name_slug)
                 logger.debug("Cleaning up old discovery topic: %s", old_topic)
                 # Publish empty payload to remove retained message
                 client.publish(old_topic, "", qos=1, retain=True)

            mqtt_data = [
                (
                    HA_DISCOVERY_PREFIX.format(ha_prefix, lamp_object.device_name),
                    lamp_object.gen_ha_config(mqtt_base_topic),
                    True,
                ),
                (
                    MQTT_BRIGHTNESS_STATE_TOPIC.format(mqtt_base_topic, lamp_object.device_name),
                    lamp_object.level,
                    False,
                ),
                (
                    MQTT_STATE_TOPIC.format(mqtt_base_topic, lamp_object.device_name),
                    MQTT_PAYLOAD_ON if lamp_object.level > 0 else MQTT_PAYLOAD_OFF,
                    False,
                ),
            ]
            
            # Only publish color temp if lamp supports it
            if lamp_object.tc is not None:
                mqtt_data.append((
                    MQTT_COLOR_TEMP_STATE_TOPIC.format(mqtt_base_topic, lamp_object.device_name),
                    lamp_object.tc,
                    False,
                ))

            # Publish Fade Time and Rate
            mqtt_data.extend([
                (
                    HA_DISCOVERY_PREFIX_NUMBER.format(ha_prefix, f"{lamp_object.device_name}_fadetime"),
                    lamp_object.gen_ha_config_fade_time(mqtt_base_topic),
                    True,
                ),
                (
                    MQTT_FADE_TIME_STATE_TOPIC.format(mqtt_base_topic, lamp_object.device_name),
                    lamp_object.fade_time,
                    False,
                ),
                (
                    HA_DISCOVERY_PREFIX_NUMBER.format(ha_prefix, f"{lamp_object.device_name}_faderate"),
                    lamp_object.gen_ha_config_fade_rate(mqtt_base_topic),
                    True,
                ),
                (
                    MQTT_FADE_RATE_STATE_TOPIC.format(mqtt_base_topic, lamp_object.device_name),
                    lamp_object.fade_rate,
                    False,
                ),
            ])
            
            for topic, payload, retain in mqtt_data:
                logger.debug("Publishing to topic: %s (retain=%s, payload_length=%d)", topic, retain, len(str(payload)))
                result = client.publish(topic, payload, qos=1, retain=retain)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    logger.error("Failed to publish to %s: %s", topic, result.rc)
                else:
                    logger.debug("Successfully published to %s", topic)

            logger.info(lamp_object)
            logger.debug("Created lamp with device_name: %s, stored in all_lamps dict", lamp_object.device_name)

        except DALIError as err:
            logger.error("While initializing <%s> @ %s: %s", name, address, err)

    for lamp in lamps:
        short_address = address.GearShort(lamp)

        await create_mqtt_lamp(
            short_address,
            devices_names_config.get_friendly_name(short_address.address),
        )

    groups = await scan_groups(driver, lamps)
    
    # Map for group/broadcast updates
    data_object["group_members"] = {}
    
    # Create mapping of integer address to Lamp object for lookups
    # Helper to get integer address from short_address (which can necessarily be obtained from the object)
    address_to_lamp = {}
    for lamp_obj in data_object["all_lamps"].values():
        if isinstance(lamp_obj.short_address, address.Short):
            # dali.address.Short has .address accessible (lines 60+)
            # But checking source code of python-dali might be safer, but previous code uses .address
             address_to_lamp[lamp_obj.short_address.address] = lamp_obj
        elif isinstance(lamp_obj.short_address, address.GearShort):
             address_to_lamp[lamp_obj.short_address.address] = lamp_obj

    for group in groups:
        logger.debug("Publishing group %d", group)

        group_address = address.Group(int(group))
        
        # Get friendly name from config for groups
        group_friendly_name = devices_names_config.get_friendly_name(group, is_group=True)

        await create_mqtt_lamp(group_address, group_friendly_name)

        # Store group members
        data_object["group_members"][group] = []
        for individual_lamp_address in groups[group]:
            if individual_lamp_address in address_to_lamp:
                data_object["group_members"][group].append(address_to_lamp[individual_lamp_address])

    # Create broadcast group to control all lamps at once
    logger.debug("Publishing broadcast group (all lamps)")
    broadcast_address = address.Broadcast()
    broadcast_friendly_name = devices_names_config.get_friendly_name("broadcast", is_group=True)
    await create_mqtt_lamp(broadcast_address, broadcast_friendly_name)
    
    # Store broadcast members (all lamps)
    data_object["group_members"]["broadcast"] = list(address_to_lamp.values())

    # Always save devices file to add any new devices (merge mode) and update group members
    devices_names_config.save_devices_names_file(data_object["all_lamps"], groups)
    
    logger.info("initialize_lamps finished")
    logger.debug("Total lamps in all_lamps dict: %d", len(data_object["all_lamps"]))
    logger.debug("Lamp device names: %s", list(data_object["all_lamps"].keys()))


def on_detect_changes_in_config(mqtt_client):
    """Callback when changes are detected in the configuration file."""
    logger.info("Reconnecting to server")
    mqtt_client.disconnect()


async def on_bridge_status_change(mqtt_client, data_object, new_status):
    """Callback when bridge health status changes.
    
    Notifies Home Assistant of bridge status changes.
    """
    mqtt_base_topic = data_object["base_topic"]
    
    # Create a status message
    status_payload = {
        "status": new_status.value,
        "timestamp": int(time.time()),
    }
    
    # Add health details if available
    if "health_monitor" in data_object:
        status_payload["health"] = data_object["health_monitor"].get_status_summary()
    
    # Create MQTT topic for bridge health status
    health_topic = f"{mqtt_base_topic}/bridge/status"
    
    logger.info("Publishing bridge status: %s", status_payload)
    try:
        import json
        result = mqtt_client.publish(
            health_topic,
            json.dumps(status_payload),
            qos=1,
            retain=True
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error("Failed to publish health status: %s", result.rc)
    except Exception as e:
        logger.error("Error publishing bridge status: %s", e)
    
def on_message_cmd_callback(mqtt_client, data_object, msg, loop):
        logger.debug("on_message_cmd_callback")
        asyncio.run_coroutine_threadsafe(on_message_cmd(mqtt_client, data_object, msg), loop)


async def on_message_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT command message."""
    logger.debug("Command on %s: %s", msg.topic, msg.payload)
    light = re.search(
        MQTT_COMMAND_TOPIC.format(data_object["base_topic"], "(.+?)"), msg.topic
    ).group(1)
    if msg.payload == MQTT_PAYLOAD_OFF:
        try:
            lamp_object = data_object["all_lamps"][light]
            logger.debug("Set light <%s> to %s", light, msg.payload)
            await lamp_object.off()
            mqtt_client.publish(
                MQTT_STATE_TOPIC.format(data_object["base_topic"], light),
                MQTT_PAYLOAD_OFF,
                retain=True,
            )

            # If this is a group/broadcast, update members in MQTT
            members = []
            if "group_" in light:
                group_match = re.search(r"group_(\d+)", light)
                if group_match:
                     group_id = int(group_match.group(1))
                     members = data_object["group_members"].get(group_id, [])
            elif "broadcast" in light:
                 members = data_object["group_members"].get("broadcast", [])
            
            for member in members:
                # Update internal state locally
                member.set_level_local(0)
                mqtt_client.publish(
                    MQTT_STATE_TOPIC.format(data_object["base_topic"], member.device_name),
                    MQTT_PAYLOAD_OFF,
                    retain=True,
                )
                mqtt_client.publish(
                   MQTT_BRIGHTNESS_STATE_TOPIC.format(data_object["base_topic"], member.device_name),
                   0,
                   retain=True
                )

        except DALIError as err:
            logger.error("Failed to set light <%s> to OFF: %s", light, err)
        except KeyError:
            logger.error("Lamp %s doesn't exists", light)
    else:
        print(msg)


async def on_message_reinitialize_lamps_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT scan lamps command message."""
    logger.debug("Reinitialize Command on %s", msg.topic)
    await initialize_lamps(data_object, mqtt_client)


async def on_message_restart_bridge_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT restart bridge command message."""
    logger.info("Restart Bridge Command on %s", msg.topic)
    import sys
    logger.warning("Restarting dali2mqtt service by request...")
    # Clean disconnect
    try:
        mqtt_client.publish(
            f"{data_object['base_topic']}/bridge/status",
            '{"status": "offline"}',
            qos=1,
            retain=True
        )
        mqtt_client.disconnect()
    except:
        pass
    sys.exit(1)


def get_lamp_object(data_object, light):
    """Retrieve lamp object from data object."""
    if light not in data_object["all_lamps"]:
        raise KeyError(f"Lamp {light} not found")
    return data_object["all_lamps"][light]

    
def on_message_brightness_cmd_callback(mqtt_client, data_object, msg, loop):
        logger.info("on_message_brightness_cmd_callback")
        asyncio.run_coroutine_threadsafe(on_message_brightness_cmd(mqtt_client, data_object, msg), loop)
        
async def on_message_brightness_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT brightness command message."""
    logger.debug("Brightness Command on %s: %s", msg.topic, msg.payload)
    light = re.search(
        MQTT_BRIGHTNESS_COMMAND_TOPIC.format(data_object["base_topic"], "(.+?)"),
        msg.topic,
    ).group(1)
    try:
        lamp_object = get_lamp_object(data_object, light)

        try:
            target_level = int(msg.payload.decode("utf-8"))
            await lamp_object.set_level(target_level)
            print(lamp_object.level)
            if lamp_object.level == 0:
                # 0 in DALI is turn off with fade out
                await lamp_object.off()
                logger.debug("Set light <%s> to OFF", light)

            mqtt_client.publish(
                MQTT_STATE_TOPIC.format(data_object["base_topic"], light),
                MQTT_PAYLOAD_ON if lamp_object.level != 0 else MQTT_PAYLOAD_OFF,
                retain=False,
            )
            mqtt_client.publish(
                MQTT_BRIGHTNESS_STATE_TOPIC.format(data_object["base_topic"], light),
                lamp_object.level,
                retain=True,
            )

            # If this is a group/broadcast, update members in MQTT
            members = []
            if "group_" in light:
                group_match = re.search(r"group_(\d+)", light)
                if group_match:
                     group_id = int(group_match.group(1))
                     members = data_object["group_members"].get(group_id, [])
            elif "broadcast" in light:
                 members = data_object["group_members"].get("broadcast", [])
            
            for member in members:
                member.set_level_local(lamp_object.level) 
                
                mqtt_client.publish(
                    MQTT_STATE_TOPIC.format(data_object["base_topic"], member.device_name),
                    MQTT_PAYLOAD_ON if member.level != 0 else MQTT_PAYLOAD_OFF,
                    retain=True,
                )
                mqtt_client.publish(
                    MQTT_BRIGHTNESS_STATE_TOPIC.format(data_object["base_topic"], member.device_name),
                    member.level,
                    retain=True
                )

        except ValueError as err:
            logger.error(
                "Can't convert <%s> to integer %d..%d: %s",
                msg.payload.decode("utf-8"),
                lamp_object.min_level,
                lamp_object.max_level,
                err,
            )
    except KeyError:
        logger.error("Lamp %s doesn't exists", light)
        
def on_message_tc_cmd_callback(mqtt_client, data_object, msg, loop):
        logger.info("on_message_tc_cmd_callback")
        asyncio.run_coroutine_threadsafe(on_message_tc_cmd(mqtt_client, data_object, msg), loop)
        
async def on_message_tc_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT TC command message."""
    logger.debug("TC Command on %s: %s", msg.topic, msg.payload)
    light = re.search(
        MQTT_COLOR_TEMP_COMMAND_TOPIC.format(data_object["base_topic"], "(.+?)"),
        msg.topic,
    ).group(1)
    try:
        lamp_object = get_lamp_object(data_object, light)

        try:
            target_tc = int(msg.payload.decode("utf-8"))
            await lamp_object.set_tc(target_tc)
            
            mqtt_client.publish(
                MQTT_COLOR_TEMP_STATE_TOPIC.format(data_object["base_topic"], light),
                lamp_object.tc,
                retain=True,
            )

            # If this is a group/broadcast, update members in MQTT
            members = []
            if "group_" in light:
                group_match = re.search(r"group_(\d+)", light)
                if group_match:
                     group_id = int(group_match.group(1))
                     members = data_object["group_members"].get(group_id, [])
            elif "broadcast" in light:
                 members = data_object["group_members"].get("broadcast", [])
            
            for member in members:
                # Only update if the member supports color temperature
                if member.tc_coolest is not None and member.tc_warmest is not None:
                    member.set_tc_local(lamp_object.tc)
                    
                    mqtt_client.publish(
                        MQTT_COLOR_TEMP_STATE_TOPIC.format(data_object["base_topic"], member.device_name),
                        member.tc,
                        retain=True,
                    )

        except ValueError as err:
            logger.error(
                "Can't convert <%s> to integer %d..%d: %s",
                msg.payload.decode("utf-8"),
                lamp_object.tc_coolest,
                lamp_object.tc_warmest,
                err,
            )
    except KeyError:
        logger.error("Lamp %s doesn't exists", light)

def on_message_fade_time_cmd_callback(mqtt_client, data_object, msg, loop):
    logger.info("on_message_fade_time_cmd_callback")
    asyncio.run_coroutine_threadsafe(on_message_fade_time_cmd(mqtt_client, data_object, msg), loop)

async def on_message_fade_time_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT Fade Time command message."""
    logger.debug("Fade Time Command on %s: %s", msg.topic, msg.payload)
    light = re.search(
        MQTT_FADE_TIME_COMMAND_TOPIC.format(data_object["base_topic"], "(.+?)"),
        msg.topic,
    ).group(1)
    try:
        lamp_object = get_lamp_object(data_object, light)
        target_val = int(msg.payload.decode("utf-8"))
        await lamp_object.set_fade_time(target_val)
        
        mqtt_client.publish(
            MQTT_FADE_TIME_STATE_TOPIC.format(data_object["base_topic"], light),
            lamp_object.fade_time,
            retain=True,
        )
    except ValueError as err:
         logger.error("Error setting fade time: %s", err)
    except KeyError:
        logger.error("Lamp %s doesn't exists", light)

def on_message_fade_rate_cmd_callback(mqtt_client, data_object, msg, loop):
    logger.info("on_message_fade_rate_cmd_callback")
    asyncio.run_coroutine_threadsafe(on_message_fade_rate_cmd(mqtt_client, data_object, msg), loop)

async def on_message_fade_rate_cmd(mqtt_client, data_object, msg):
    """Callback on MQTT Fade Rate command message."""
    logger.debug("Fade Rate Command on %s: %s", msg.topic, msg.payload)
    light = re.search(
        MQTT_FADE_RATE_COMMAND_TOPIC.format(data_object["base_topic"], "(.+?)"),
        msg.topic,
    ).group(1)
    try:
        lamp_object = get_lamp_object(data_object, light)
        target_val = int(msg.payload.decode("utf-8"))
        await lamp_object.set_fade_rate(target_val)
        
        mqtt_client.publish(
            MQTT_FADE_RATE_STATE_TOPIC.format(data_object["base_topic"], light),
            lamp_object.fade_rate,
            retain=True,
        )
    except ValueError as err:
         logger.error("Error setting fade rate: %s", err)
    except KeyError:
        logger.error("Lamp %s doesn't exists", light)


def on_message(mqtt_client, data_object, msg):  # pylint: disable=W0613
    """Default callback on MQTT message."""
    logger.error("Don't publish to %s", msg.topic)


async def on_connect(
    client,
    data_object,
    flags,
    result,
    ha_prefix=DEFAULT_HA_DISCOVERY_PREFIX,
):  # pylint: disable=W0613,R0913
    """Callback on connection to MQTT server."""
    mqtt_base_topic = data_object["base_topic"]
    client.subscribe(
        [
            (MQTT_COMMAND_TOPIC.format(mqtt_base_topic, "+"), 0),
            (MQTT_BRIGHTNESS_COMMAND_TOPIC.format(mqtt_base_topic, "+"), 0),
            (MQTT_COLOR_TEMP_COMMAND_TOPIC.format(mqtt_base_topic, "+"), 0),
            (MQTT_FADE_TIME_COMMAND_TOPIC.format(mqtt_base_topic, "+"), 0),
            (MQTT_FADE_RATE_COMMAND_TOPIC.format(mqtt_base_topic, "+"), 0),
            (MQTT_SCAN_LAMPS_COMMAND_TOPIC.format(mqtt_base_topic), 0),
            (f"{mqtt_base_topic}/bridge/request/restart", 0),
        ]
    )
    client.publish(
        MQTT_DALI2MQTT_STATUS.format(mqtt_base_topic), MQTT_AVAILABLE, retain=True
    )
    
    # Publish initial bridge health status
    health_topic = f"{mqtt_base_topic}/bridge/status"
    client.publish(
        health_topic,
        '{"status": "online", "timestamp": ' + str(int(time.time())) + '}',
        qos=1,
        retain=True
    )
    
    await initialize_lamps(data_object, client)

def on_connect_callback(a, b, c, d, ha_prefix, loop):
        logger.info("on_connect_callback")
        asyncio.run_coroutine_threadsafe(on_connect(a, b, c, d, ha_prefix), loop)


async def create_mqtt_client(
    driver_manager,
    mqtt_server,
    mqtt_port,
    mqtt_username,
    mqtt_password,
    mqtt_base_topic,
    devices_names_config,
    ha_prefix,
    log_level,
):
    """Create MQTT client object, setup callbacks and connection to server.
    
    Args:
        driver_manager: DriverManager instance for DALI operations
        mqtt_server: MQTT server address
        mqtt_port: MQTT server port
        mqtt_username: MQTT username
        mqtt_password: MQTT password
        mqtt_base_topic: Base topic for MQTT
        devices_names_config: Device names configuration
        ha_prefix: Home Assistant discovery prefix
        log_level: Logging level
    """
    logger.info("Connecting to %s:%s", mqtt_server, mqtt_port)
    mqttc = mqtt.Client(
        client_id="dali2mqtt",
        userdata={
            "driver": driver_manager,
            "base_topic": mqtt_base_topic,
            "ha_prefix": ha_prefix,
            "devices_names_config": devices_names_config,
            "log_level": log_level,
            "all_lamps": {},
        },
    )
    mqttc.will_set(
        MQTT_DALI2MQTT_STATUS.format(mqtt_base_topic), MQTT_NOT_AVAILABLE, retain=True
    )
    loop = asyncio.get_event_loop()

    # client.on_connect = on_connect_callback
    mqttc.on_connect = lambda a, b, c, d: on_connect_callback(a, b, c, d, ha_prefix, loop)

    # Add message callbacks that will only trigger on a specific subscription match.
    mqttc.message_callback_add(
        MQTT_COMMAND_TOPIC.format(mqtt_base_topic, "+"), lambda a,b,c : on_message_cmd_callback(a,b,c,loop)
    )
    
    mqttc.message_callback_add(
        MQTT_BRIGHTNESS_COMMAND_TOPIC.format(mqtt_base_topic, "+"),
        lambda a,b,c : on_message_brightness_cmd_callback(a,b,c,loop),
    )
    
    mqttc.message_callback_add(
        MQTT_COLOR_TEMP_COMMAND_TOPIC.format(mqtt_base_topic, "+"),
        lambda a,b,c : on_message_tc_cmd_callback(a,b,c,loop),
    )
    
    mqttc.message_callback_add(
        MQTT_FADE_TIME_COMMAND_TOPIC.format(mqtt_base_topic, "+"),
        lambda a,b,c : on_message_fade_time_cmd_callback(a,b,c,loop),
    )
    
    mqttc.message_callback_add(
        MQTT_FADE_RATE_COMMAND_TOPIC.format(mqtt_base_topic, "+"),
        lambda a,b,c : on_message_fade_rate_cmd_callback(a,b,c,loop),
    )
    
    mqttc.message_callback_add(
        MQTT_SCAN_LAMPS_COMMAND_TOPIC.format(mqtt_base_topic),
        lambda a,b,c : asyncio.run_coroutine_threadsafe(on_message_reinitialize_lamps_cmd(a,b,c), loop),
    )
    
    mqttc.message_callback_add(
        f"{mqtt_base_topic}/bridge/request/restart",
        lambda a,b,c : asyncio.run_coroutine_threadsafe(on_message_restart_bridge_cmd(a,b,c), loop),
    )

    mqttc.on_message = on_message
    if mqtt_username:
        mqttc.username_pw_set(mqtt_username, mqtt_password)
    mqttc.connect(mqtt_server, mqtt_port, 60)
    return mqttc


async def main(args):
    """Main loop."""
    mqttc = None
    driver_manager = None
    health_monitor = None
    monitoring_task = None
    
    config = Config(args, lambda: on_detect_changes_in_config(mqttc))

    if config.log_color:
        logging.addLevelName(
            logging.WARNING,
            "{}{}".format(YELLOW_COLOR, logging.getLevelName(logging.WARNING)),
        )
        logging.addLevelName(
            logging.ERROR, "{}{}".format(RED_COLOR, logging.getLevelName(logging.ERROR))
        )

    logger.setLevel(ALL_SUPPORTED_LOG_LEVELS[config.log_level])
    
    # Suppress verbose timeout errors from python-dali driver when bus is down
    # These logs ("faking an error response") are expected when DALI power is off
    logging.getLogger("dali.driver.hid").setLevel(logging.CRITICAL)
    logging.getLogger("tridonic").setLevel(logging.CRITICAL)
    logging.getLogger("hasseb").setLevel(logging.CRITICAL) 

    devices_names_config = DevicesNamesConfig(
        config.log_level, config.devices_names_file
    )

    dali_driver = None
    logger.debug("Using <%s> driver", config.dali_driver)

    if config.dali_driver == HID_HASSEB:
        from dali.driver.hid import hasseb

        dali_driver = hasseb("/dev/dali/hasseb-*", glob=True)
        dali_driver.connect()
        logger.info("Waiting for device to be connected...")
        await dali_driver.connected.wait()
        if float(dali_driver.firmware_version) < MIN_HASSEB_FIRMWARE_VERSION:
            logger.error("Using dali2mqtt requires newest hasseb firmware")
            logger.error("Current firmware: %s < Required: %s", dali_driver.firmware_version, MIN_HASSEB_FIRMWARE_VERSION)
            logger.error(
                "Please, look at https://github.com/hasseb/python-dali/tree/master/dali/driver/hasseb_firmware"
            )
            quit(1)
        logger.info("Firmware: %s",dali_driver.firmware_version)
        
    elif config.dali_driver == HID_TRIDONIC:
        from dali.driver.hid import tridonic

        dali_driver = tridonic("/dev/dali/daliusb-*", glob=True)
        dali_driver.connect()
        logger.info("Waiting for device to be connected...")
        await dali_driver.connected.wait()
        logger.info("Firmware: %s",dali_driver.firmware_version)

    # Initialize driver manager for automatic reconnection
    driver_manager = DriverManager(dali_driver, config.dali_driver)
    
    # Initialize health monitor
    health_monitor = HealthMonitor(check_interval=30)
    
    # Link health monitor to driver manager
    driver_manager.health_monitor = health_monitor

    retries = 0
    while retries < MAX_RETRIES:
        try:
            mqttc = await create_mqtt_client(
                driver_manager,
                *config.mqtt_conf,
                devices_names_config,
                config.ha_discovery_prefix,
                config.log_level,
            )
            
            # Add health monitor and driver manager to data
            mqttc.user_data_set({
                **mqttc._userdata,
                "health_monitor": health_monitor,
                "driver_manager": driver_manager,
            })
            
            # Start health monitoring
            status_callback = lambda status: on_bridge_status_change(
                mqttc, mqttc._userdata, status
            )
            monitoring_task = asyncio.create_task(
                health_monitor.start_monitoring(status_callback)
            )
            
            logger.info("dali2mqtt bridge started successfully")
            mqttc.loop_start()
            
            unhealthy_since = None
            bus_error_since = None
            while True:
                await asyncio.sleep(60)  # Check every 60s
                
                # Ping DALI to verify connection (and keep health monitor updated)
                try:
                    # Query valid address 0 (harmless query)
                    response = await driver_manager.send(gear.QueryControlGearPresent(address.Short(0)))
                    
                    # Check for DALI Bus Error (Timeout/Fake Error from driver)
                    # When Tridonic/Hasseb driver times out (DALI power off), it may return BackwardFrameError(255) if configured to fake response
                    # or the response value itself might indicate error.
                    is_bus_error = False
                    if hasattr(response, 'value') and isinstance(response.value, dali.frame.BackwardFrameError):
                         is_bus_error = True
                    elif isinstance(response, dali.frame.BackwardFrameError):
                         is_bus_error = True
                    
                    if is_bus_error:
                         if bus_error_since is None:
                              bus_error_since = time.time()
                              logger.warning("DALI Bus Error detected (Timeout/No Power). DALI Bus might be unpowered.")
                              # Update Status to reflect Bus Error
                              try:
                                  mqttc.publish(f"{config.mqtt_conf[2]}/bridge/status", '{"status": "online", "bus_error": true}', qos=1, retain=True)
                              except:
                                  pass
                         elif time.time() - bus_error_since > 300: # Remind every 5 mins
                              logger.warning("DALI Bus Error persisting (Power still off?)")
                              bus_error_since = time.time()
                    else:
                         if bus_error_since is not None:
                              logger.info("DALI Bus recovered.")
                              try:
                                  mqttc.publish(f"{config.mqtt_conf[2]}/bridge/status", '{"status": "online", "bus_error": false}', qos=1, retain=True)
                              except:
                                  pass
                              bus_error_since = None

                except Exception:
                    pass # Health monitor records failure automatically
                
                # Check for persistent health issues
                if health_monitor.status != BridgeStatus.ONLINE:
                    if unhealthy_since is None:
                        unhealthy_since = time.time()
                        logger.warning("Bridge status is %s. Monitoring for recovery...", health_monitor.status.value)
                    
                    # Check if connection to driver is actually lost
                    # If driver is connected but DALI is down, we don't want to restart loop
                    driver_dead = not driver_manager.get_connection_status()["connected"]
                    
                    if driver_dead:
                        # USB/Driver failure - Restarting service is the best fix
                        if time.time() - unhealthy_since > 120:
                            logger.error("Driver disconnected/dead for over 2 minutes. Exiting to trigger restart.")
                            try:
                                mqttc.disconnect()
                                mqttc.loop_stop()
                            except:
                                pass
                            return # Exit main() to trigger restart
                    else:
                        # USB is connected, but DALI commands failing (Bus power off?)
                        # Log periodically but stay online to avoid boot loops/log spam
                        if (time.time() - unhealthy_since) > 120 and (time.time() - unhealthy_since) % 300 < 30:
                             logger.warning("DALI Bus communication failing (Power off?), but Driver is connected. Staying online.")
                else:
                    unhealthy_since = None
                
            retries = 0  # if we reach here, it means we where already connected successfully
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
            break
        except Exception as e:
            logger.debug(e)
            logger.error("%s: %s", type(e).__name__, e)
            health_monitor.status = BridgeStatus.OFFLINE
            
            # Try to publish offline status before disconnecting
            if mqttc:
                try:
                    mqttc.publish(
                        f"{config.mqtt_conf[2]}/bridge/status",
                        '{"status": "offline"}',
                        qos=1,
                        retain=True
                    )
                    mqttc.loop_stop()
                except Exception as pub_err:
                    logger.debug("Could not publish offline status: %s", pub_err)
            
            time.sleep(random.randint(MIN_BACKOFF_TIME, MAX_BACKOFF_TIME))
            retries += 1
    
    # Cleanup
    if monitoring_task:
        health_monitor.stop_monitoring()
        try:
            await asyncio.wait_for(monitoring_task, timeout=2)
        except asyncio.TimeoutError:
            monitoring_task.cancel()
    
    if mqttc:
        try:
            mqttc.loop_stop()
        except Exception as e:
            logger.debug("Error stopping MQTT loop: %s", e)

    logger.error("Maximum retries of %d reached, exiting...", retries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)
    parser.add_argument(
        f"--{CONF_CONFIG}", help="configuration file", default=DEFAULT_CONFIG_FILE
    )
    parser.add_argument(
        f"--{CONF_DEVICES_NAMES_FILE.replace('_','-')}", help="devices names file"
    )
    parser.add_argument(f"--{CONF_MQTT_SERVER.replace('_','-')}", help="MQTT server")
    parser.add_argument(
        f"--{CONF_MQTT_PORT.replace('_','-')}", help="MQTT port", type=int
    )
    parser.add_argument(
        f"--{CONF_MQTT_USERNAME.replace('_','-')}", help="MQTT username"
    )
    parser.add_argument(
        f"--{CONF_MQTT_PASSWORD.replace('_','-')}", help="MQTT password"
    )
    parser.add_argument(
        f"--{CONF_MQTT_BASE_TOPIC.replace('_','-')}", help="MQTT base topic"
    )
    parser.add_argument(
        f"--{CONF_DALI_DRIVER.replace('_','-')}",
        help="DALI device driver",
        choices=DALI_DRIVERS,
    )
    parser.add_argument(
        f"--{CONF_DALI_LAMPS.replace('_','-')}",
        help="Number of lamps to scan",
        type=int,
    )
    parser.add_argument(
        f"--{CONF_HA_DISCOVERY_PREFIX.replace('_','-')}",
        help="HA discovery mqtt prefix",
    )
    parser.add_argument(
        f"--{CONF_LOG_LEVEL.replace('_','-')}",
        help="Log level",
        choices=ALL_SUPPORTED_LOG_LEVELS,
    )
    parser.add_argument(
        f"--{CONF_LOG_COLOR.replace('_','-')}",
        help="Coloring output",
        action="store_true",
    )

    args = parser.parse_args()

    # main(args)
    #logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main(args))

