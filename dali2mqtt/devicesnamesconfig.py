"""Configuration Object."""
import logging

import yaml
from dali2mqtt.consts import ALL_SUPPORTED_LOG_LEVELS, LOG_FORMAT

logging.basicConfig(format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class DevicesNamesConfigLoadError(Exception):
    """Exception class for DevicesNamesConfig."""

    pass


class DevicesNamesConfig:
    """Devices Names Configuration."""

    def __init__(self, log_level, filename):
        """Initialize devices names config."""
        self._path = filename
        self._devices_names = {}

        logger.setLevel(ALL_SUPPORTED_LOG_LEVELS[log_level])
        # Load from file
        try:
            self.load_devices_names_file()
        except FileNotFoundError:
            logger.info("No device names config, creating new one")
            with open(self._path, "w"):
                pass

    def load_devices_names_file(self):
        """Load configuration from yaml file."""
        try:
            with open(self._path, "r") as infile:
                logger.debug("Loading devices names from <%s>", self._path)
                self._devices_names = yaml.safe_load(infile) or {}
        except yaml.YAMLError as error:
            logger.error("In devices file %s: %s", self._path, error)
            raise DevicesNamesConfigLoadError()
        except Exception:
            logger.error(
                "Could not load device names config <%s>, a new one will be created after successfull start",
                self._path,
            )

    def save_devices_names_file(self, all_lamps):
        """Save configuration back to yaml file (merge mode - add new, keep existing)."""
        # Load existing config to preserve custom names
        existing_devices = dict(self._devices_names) if self._devices_names else {}
        
        logger.debug("Saving devices to %s (currently %d devices in config)", self._path, len(existing_devices))
        
        # Add any new devices that aren't in the config
        new_devices_added = 0
        for lamp_object in all_lamps.values():
            # Handle GearShort (has .address), GearGroup (has .group), and Broadcast addresses
            if hasattr(lamp_object.short_address, 'address'):
                address_value = lamp_object.short_address.address
                config_key = str(address_value)
                default_name = str(address_value)
            elif hasattr(lamp_object.short_address, 'group'):
                address_value = lamp_object.short_address.group
                config_key = f"group_{address_value}"
                default_name = f"group_{address_value}"
            elif str(type(lamp_object.short_address).__name__) == 'Broadcast':
                config_key = "group_broadcast"
                default_name = "All Lights"
            else:
                continue
            
            # Only add if not already in config (preserves custom names)
            if config_key not in existing_devices:
                existing_devices[config_key] = {
                    "friendly_name": default_name
                }
                new_devices_added += 1
                logger.info("Added new device %s to devices.yaml", config_key)
        
        if new_devices_added == 0:
            logger.info("No new devices to add to %s", self._path)
        else:
            logger.info("Adding %d new devices to %s", new_devices_added, self._path)
        
        self._devices_names = existing_devices
        try:
            with open(self._path, "w") as outfile:
                yaml.dump(
                    self._devices_names,
                    outfile,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            logger.info("Successfully saved %d devices to %s", len(self._devices_names), self._path)
        except Exception as err:
            logger.error("Could not save device names config: %s", err)

    def is_devices_file_empty(self) -> bool:
        """Check if we have any device configured."""
        return len(self._devices_names) == 0

    def get_friendly_name(self, short_address_value, is_group=False) -> str:
        """Retrieve friendly_name."""
        # For groups, lookup with 'group_X' key
        if is_group:
            config_key = f"group_{short_address_value}"
            if config_key in self._devices_names:
                return self._devices_names[config_key].get(
                    "friendly_name", config_key
                )
            # Return friendly default for broadcast
            if short_address_value == "broadcast":
                return "All Lights"
            return f"group_{short_address_value}"
        
        # For lamps, lookup with numeric key
        config_key = str(short_address_value)
        if config_key in self._devices_names:
            return self._devices_names[config_key].get(
                "friendly_name", config_key
            )
        return str(short_address_value)
