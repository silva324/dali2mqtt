# Using DALI2MQTT Docker Image from GitHub Container Registry

This guide shows how to use the pre-built dali2mqtt Docker image from GitHub Container Registry (ghcr.io).

## Available Images

- **x86/amd64:** `ghcr.io/silva324/dali2mqtt:latest`
- **ARM64 (Raspberry Pi 4):** `ghcr.io/silva324/dali2mqtt:latest-arm64`
- **Specific version:** `ghcr.io/silva324/dali2mqtt:v1.0.0`

## Prerequisites

1. Docker installed on your system
2. DALI USB interface connected to your device
3. MQTT broker running (e.g., Mosquitto)
4. Configuration files prepared

## Install Docker on Raspberry Pi (RPi 3/4)

Use these steps to install Docker on Raspberry Pi OS (Debian-based). Works on 32-bit and 64-bit; prefer 64-bit if you plan to use ARM64 images.

### 1. Update the system

```bash
sudo apt update
sudo apt upgrade -y
```

### 2. Install Docker (official convenience script)

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

### 3. Enable and start Docker

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

### 4. Allow your user to run Docker without sudo

```bash
sudo usermod -aG docker $USER
``` 

Log out of SSH and back in (or reboot) to apply group changes.

### 5. Verify Docker is working

```bash
docker --version
docker run --rm hello-world
```

### 6. Install Docker Compose v2 (plugin)

Modern Docker uses the Compose V2 plugin (`docker compose`). If the command below fails, install the plugin.

```bash
docker compose version || sudo apt install -y docker-compose-plugin
docker compose version
```

Notes:
- Use `docker compose` (with a space) for Compose V2. The legacy `docker-compose` (hyphen) is deprecated.
- On Raspberry Pi OS Bookworm, `docker-compose-plugin` is available via APT.

## Configuration Files

### 1. Create configuration directory

```bash
mkdir -p ~/dali2mqtt/config
mkdir -p ~/dali2mqtt/data
```

### 2. Create `config.yaml`

Create `~/dali2mqtt/config/config.yaml` with your settings:

```yaml
dali_driver: hid_tridonic  # or hid_hasseb
devices_names: devices.yaml
ha_discovery_prefix: homeassistant
log_color: false
log_level: info
mqtt_base_topic: dali2mqtt
mqtt_port: 1883
mqtt_server: 192.168.1.100  # Your MQTT broker IP
mqtt_username: your_username  # Optional
mqtt_password: your_password  # Optional
```

### 3. Create `devices.yaml` (optional)

Create `~/dali2mqtt/config/devices.yaml` to assign friendly names to your DALI lamps:

```yaml
0:
  friendly_name: "Living Room Ceiling"
1:
  friendly_name: "Kitchen Counter"
2:
  friendly_name: "Bedroom Main Light"
```

## Running with Docker CLI

### Basic usage (x86/amd64)

```bash
docker run -d \
  --name dali2mqtt \
  --privileged \
  -v ~/dali2mqtt/config:/app/config \
  -v ~/dali2mqtt/data:/app/data \
  --restart unless-stopped \
  ghcr.io/silva324/dali2mqtt:latest
```

### For Raspberry Pi 4 (ARM64)

```bash
docker run -d \
  --name dali2mqtt \
  --privileged \
  -v ~/dali2mqtt/config:/app/config \
  -v ~/dali2mqtt/data:/app/data \
  --restart unless-stopped \
  ghcr.io/silva324/dali2mqtt:latest-arm64
```

### With specific USB device (more secure than privileged)

```bash
docker run -d \
  --name dali2mqtt \
  --device=/dev/bus/usb \
  -v ~/dali2mqtt/config:/app/config \
  -v ~/dali2mqtt/data:/app/data \
  --restart unless-stopped \
  ghcr.io/silva324/dali2mqtt:latest
```

## Running with Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  dali2mqtt:
    image: ghcr.io/silva324/dali2mqtt:latest  # or :latest-arm64 for Raspberry Pi 4
    container_name: dali2mqtt
    privileged: true  # Required for USB access
    restart: unless-stopped
    volumes:
      - ./config:/app/config
      - ./data:/app/data
    environment:
      - TZ=Europe/Lisbon  # Your timezone
    # Alternative to privileged mode (more secure):
    # devices:
    #   - /dev/bus/usb:/dev/bus/usb
```

### Start the service

```bash
docker-compose up -d
```

### View logs

```bash
docker-compose logs -f dali2mqtt
```

### Stop the service

```bash
docker-compose down
```

## Volume Mounts Explained

- **`/app/config`** - Contains your configuration files:
  - `config.yaml` - Main configuration
  - `devices.yaml` - Device friendly names (optional)

- **`/app/data`** - Persistent data directory for storing state and cache

## Common Commands

### Check container status
```bash
docker ps -a | grep dali2mqtt
```

### View real-time logs
```bash
docker logs -f dali2mqtt
```

### Restart container
```bash
docker restart dali2mqtt
```

### Stop and remove container
```bash
docker stop dali2mqtt
docker rm dali2mqtt
```

### Update to latest version
```bash
docker pull ghcr.io/silva324/dali2mqtt:latest
docker stop dali2mqtt
docker rm dali2mqtt
# Then run the docker run command again
```

Or with docker-compose:
```bash
docker-compose pull
docker-compose up -d
```

## Troubleshooting

### Container won't start
```bash
# Check logs for errors
docker logs dali2mqtt

# Verify config file syntax
cat ~/dali2mqtt/config/config.yaml
```

### USB device not detected
```bash
# List USB devices on host
lsusb

# Run container in privileged mode
# Already included in examples above
```

### MQTT connection issues
```bash
# Test MQTT connection from host
mosquitto_pub -h YOUR_MQTT_IP -t test -m "hello"

# Verify MQTT settings in config.yaml
cat ~/dali2mqtt/config/config.yaml | grep mqtt
```

### Permission issues
```bash
# Ensure correct ownership of config directory
sudo chown -R 1000:1000 ~/dali2mqtt/config
sudo chown -R 1000:1000 ~/dali2mqtt/data
```

## Integration with Home Assistant

Once running, dali2mqtt will automatically discover DALI lamps in Home Assistant via MQTT discovery.

**MQTT Topics:**
- `dali2mqtt/status` - Daemon status
- `dali2mqtt/light/<address>/state` - Light state
- `dali2mqtt/light/<address>/set` - Control light
- `homeassistant/light/dali2mqtt/<address>/config` - Auto-discovery

Check Home Assistant → Settings → Devices & Services → MQTT for discovered lights.

## Security Notes

- **Privileged mode** gives the container full access to the host. Use `--device` mount for better security when possible.
- Store sensitive credentials in `config.yaml` with appropriate file permissions.
- Consider using Docker secrets for production deployments.

## GitHub Container Registry Authentication

Public images don't require authentication, but if you encounter rate limits:

```bash
# Login to GitHub Container Registry
echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

## Support

For issues and questions:
- GitHub Issues: https://github.com/silva324/dali2mqtt/issues
- Original Project: https://github.com/dgomes/dali2mqtt
