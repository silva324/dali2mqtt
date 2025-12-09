# DALI2MQTT Docker Guide

This guide explains how to run dali2mqtt using Docker and Docker Compose for optimal efficiency.

## Quick Start

1. **Copy the environment configuration:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` with your MQTT broker settings:**
   ```bash
   nano .env
   ```
   Update at minimum:
   - `MQTT_SERVER` - Your MQTT broker IP/hostname
   - `MQTT_USERNAME` - Your MQTT username (if required)
   - `MQTT_PASSWORD` - Your MQTT password (if required)

3. **Create config directory and customize if needed:**
   ```bash
   mkdir -p config data
   cp config.yaml config/config.yaml
   ```

4. **Build and start the container:**
   ```bash
   docker-compose up -d
   ```

5. **Check the logs:**
   ```bash
   docker-compose logs -f
   ```

## Configuration

### Environment Variables

All configuration can be done via environment variables in the `.env` file:

- **MQTT Settings:**
  - `MQTT_SERVER` - MQTT broker address (default: localhost)
  - `MQTT_PORT` - MQTT broker port (default: 1883)
  - `MQTT_USERNAME` - MQTT username (optional)
  - `MQTT_PASSWORD` - MQTT password (optional)
  - `MQTT_BASE_TOPIC` - Base topic for MQTT messages (default: dali2mqtt)

- **DALI Settings:**
  - `DALI_DRIVER` - DALI USB driver (hid_hasseb or hid_tridonic)
  - `DALI_LAMPS` - Number of lamps to scan (default: 64)

- **Home Assistant:**
  - `HA_DISCOVERY_PREFIX` - MQTT discovery prefix (default: homeassistant)

- **Logging:**
  - `LOG_LEVEL` - Log verbosity (debug, info, warning, error, critical)
  - `LOG_COLOR` - Enable colored output (set to false for Docker)

### Configuration File

Alternatively, you can use the `config/config.yaml` file for more detailed configuration.

## Docker Commands

### Start the service
```bash
docker-compose up -d
```

### Stop the service
```bash
docker-compose down
```

### View logs
```bash
docker-compose logs -f
```

### Restart the service
```bash
docker-compose restart
```

### Rebuild after code changes
```bash
docker-compose up -d --build
```

### Check container status
```bash
docker-compose ps
```

## USB Device Access

The container runs in privileged mode to access USB DALI devices. Ensure your DALI USB interface is connected before starting the container.

To check if the device is detected:
```bash
docker-compose exec dali2mqtt ls -l /dev/hidraw*
```

## Volumes

The following directories are mounted:
- `./config` - Configuration files
- `./data` - Device names and persistent data
- `/dev` - USB device access

## Architecture

The Docker image uses a multi-stage build for efficiency:
- **Builder stage:** Compiles dependencies with build tools
- **Runtime stage:** Minimal image with only runtime dependencies

**Image size:** ~200MB (vs ~800MB+ for full Python image)

**Features:**
- Based on Python 3.11-slim for minimal footprint
- Non-root user for security
- Health checks for monitoring
- Optimized layer caching
- No build artifacts in final image

## Troubleshooting

### Container won't start
Check logs: `docker-compose logs`

### Can't connect to MQTT
- Verify MQTT_SERVER is correct in `.env`
- Ensure MQTT broker is accessible from Docker network
- Check firewall settings

### DALI device not found
- Ensure USB device is connected
- Check USB permissions with `ls -l /dev/hidraw*`
- Try restarting the container: `docker-compose restart`

### View container resource usage
```bash
docker stats dali2mqtt
```

## Integration with Home Assistant

If running Home Assistant in Docker on the same host:

1. Add both to the same Docker network, or
2. Use `network_mode: host` (current default), or
3. Point MQTT_SERVER to your MQTT broker's network address

The service will auto-discover devices in Home Assistant via MQTT discovery.

## Performance

The container is optimized for:
- **Low memory usage:** ~50-100MB RAM
- **Fast startup:** <10 seconds
- **Minimal CPU:** <1% idle, <5% during operations
- **Small image size:** ~200MB

## Updates

To update to the latest version:
```bash
git pull
docker-compose up -d --build
```

## Security Notes

- Container runs as non-root user (uid 1000)
- Privileged mode is required for USB access
- For production, consider using specific device mappings instead of privileged mode
- Store credentials in `.env` file (not committed to git)
