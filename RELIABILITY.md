# DALI2MQTT Reliability & Robustness Guide

This guide explains the reliability features implemented in dali2mqtt to ensure consistent operation and automatic recovery from failures.

## Features

### 1. **Automatic USB Reconnection**

The system automatically detects when the DALI USB device disconnects and attempts to reconnect with exponential backoff.

**How it works:**
- `DriverManager` class monitors DALI driver connection status
- On disconnection, it initiates automatic reconnection attempts
- Uses exponential backoff: 2s, 4s, 8s, 16s, 32s (max 30s)
- Maximum 5 reconnection attempts before flagging as failed
- Logs all reconnection attempts for debugging

**Configuration:**
```python
# In dali2mqtt.py main():
driver_manager = DriverManager(dali_driver, config.dali_driver, max_reconnect_attempts=5)
```

### 2. **Health Monitoring**

The system continuously monitors bridge health and detects issues automatically.

**Health Metrics:**
- **DALI Connectivity**: Tracks successful/failed DALI commands
- **MQTT Connectivity**: Monitors MQTT publish operations
- **Status Transitions**: Detects when bridge becomes degraded or offline

**Status States:**
- `online` - System fully operational
- `degraded` - One or more subsystems not responding
- `offline` - Complete system failure
- `reconnecting` - Attempting to restore connectivity

**Check Interval:** 30 seconds (configurable)

**Consecutive Failures:** 3 failed DALI commands trigger degraded state

```python
# In dali2mqtt.py main():
health_monitor = HealthMonitor(check_interval=30)
```

### 3. **Home Assistant Bridge Status Notification**

Bridge health status is published to Home Assistant via MQTT, allowing you to:
- Create automations based on bridge state
- Get alerts when the bridge fails
- Monitor system reliability over time

**MQTT Topic:** `{mqtt_base_topic}/bridge/health`

**Payload Example:**
```json
{
  "status": "online",
  "timestamp": 1234567890,
  "health": {
    "status": "online",
    "dali_ok": true,
    "mqtt_ok": true,
    "consecutive_failures": 0,
    "issues": ["System operational"],
    "time_since_dali": 5,
    "time_since_mqtt": 3
  }
}
```

### 4. **Automatic Retry Logic**

Commands are automatically retried on failure without losing state.

**Features:**
- Retries DALI commands on USB/communication errors
- Waits between retry attempts (0.5s, 1s, 1.5s)
- Logs all retry attempts for debugging
- Graceful fallback if retries exhausted

**Usage:**
```python
# In Lamp class - uses driver manager automatically:
try:
    response = await lamp_object.set_level(brightness)
except DALIError:
    logger.error("Failed to set level after retries")
```

## Usage in Home Assistant

### Create Automations Based on Bridge Status

**Example: Alert when bridge goes offline**
```yaml
automation:
  - alias: "DALI Bridge Offline Alert"
    trigger:
      platform: mqtt
      topic: dali2mqtt/bridge/health
      payload: '{"status": "offline"}'
    action:
      - service: notify.mobile_app
        data:
          message: "DALI Bridge is offline!"
          title: "System Alert"
```

**Example: Auto-restart on failure**
```yaml
automation:
  - alias: "Check DALI Bridge Health"
    trigger:
      platform: time_pattern
      minutes: "/5"  # Every 5 minutes
    condition:
      platform: template
      value_template: "{{ state_attr('sensor.dali_bridge_health', 'status') == 'offline' }}"
    action:
      - service: shell_command.restart_dali2mqtt
```

### Monitor Bridge Health in Dashboard

Create a template sensor to track bridge status:

```yaml
template:
  - sensor:
      - name: "DALI Bridge Health"
        unique_id: dali_bridge_health
        state_topic: "dali2mqtt/bridge/health"
        value_template: "{{ value_json.status }}"
        attributes:
          dali_ok: "{{ value_json.health.dali_ok }}"
          mqtt_ok: "{{ value_json.health.mqtt_ok }}"
          failures: "{{ value_json.health.consecutive_failures }}"
          issues: "{{ value_json.health.issues }}"
```

## Systemd Service Configuration

The existing systemd service (`dali2mqtt.service`) already has restart capabilities:

```ini
[Service]
Restart=always
RestartSec=10
```

This ensures the service automatically restarts if it exits unexpectedly.

**Optional: Restart on specific failure patterns**
```ini
[Service]
Restart=always
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5
```

This limits restarts to 5 times in 300 seconds to prevent restart loops.

## Troubleshooting

### Bridge Repeatedly Goes Offline

**Check logs:**
```bash
journalctl -u dali2mqtt -f
# or
tail -f /var/log/dali2mqtt.log
```

**Likely causes:**
1. **USB Device Issue**: Check if device shows up with `lsusb`
2. **Udev Rules**: Verify `/etc/udev/rules.d/50-hasseb.rules` is correct
3. **MQTT Connection**: Check MQTT broker connectivity
4. **Lamp Communication**: Verify DALI wiring and power

### Bridge Shows Degraded Status

**Check health details:**
```bash
mosquitto_sub -t "dali2mqtt/bridge/health"
```

**Indicates which subsystem is failing:**
- `dali_ok: false` - USB/DALI communication issues
- `mqtt_ok: false` - MQTT connectivity issues

### Excessive Reconnection Attempts

**Monitor reconnection logs:**
```bash
journalctl -u dali2mqtt | grep -i "reconnect"
```

**Solutions:**
1. Check USB cable connection
2. Verify USB device has power
3. Check system dmesg for USB errors: `dmesg | grep -i usb`
4. Try unplugging/replugging USB device

## Performance Impact

- **Health Monitoring**: ~1% CPU, negligible memory
- **Retry Logic**: No impact unless errors occur
- **MQTT Updates**: Only published on state changes

## Monitoring in Production

**Recommended checks:**
1. Monitor `dali2mqtt/bridge/health` topic regularly
2. Set up alerts for `status != "online"`
3. Log bridge restarts for trend analysis
4. Check systemd journal weekly: `journalctl -u dali2mqtt --since "1 week ago" | grep -i error`

## Future Enhancements

- [ ] Periodic self-test (send test commands to verify operability)
- [ ] Automatic lamp state recovery after reconnection
- [ ] Metrics publishing (uptime, command success rate)
- [ ] Predictive diagnostics (warning before complete failure)

## Code Structure

**New modules:**

- **`health_monitor.py`**: System health tracking and status management
- **`driver_manager.py`**: DALI driver lifecycle and reconnection logic

**Modified files:**
- **`dali2mqtt.py`**: Integrated health monitoring and driver management
- **`lamp.py`**: Added error handling in init

**No breaking changes** - All existing functionality preserved with additional reliability.
