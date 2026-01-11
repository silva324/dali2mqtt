
# DALI2MQTT Quick Guide

## Run on host



### Prerequisites
- Raspberry Pi OS (64-bit) installed and updated
- Docker installed
- DALI USB Interface (HID) connected
- Clone dali2mqtt repository

1. dali2mqtt

```bash
git clone https://silva324:<TOKEN>@github
.com/silva324/dali2mqtt.git
``` 
 
2. python-dalo

```bash
git clone https://silva324:<TOKEN>@github.com/silva324/python-dali
```

### Setup udev rules

sudo cp 50-hasseb.rules /etc/udev/rules.d/50-dali-hid.rules
sudo usermod -aG dialout pi
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -l /dev/hidraw*
ls -l /dev/dali/

### Stop old container
docker stop dali2mqtt
docker rm dali2mqtt

### Rebuild with private repo (optional if LOCAL)
docker build -t ghcr.io/silva324/dali2mqtt:latest-arm64-local \
  --build-arg VERSION=local \
  -f Dockerfile .

### Run
docker run -d \
  --name dali2mqtt \
  --privileged \
  -v /dev:/dev
  -v ~/dali2mqtt/config:/app/config \
  -v ~/dali2mqtt/data:/app/data \
  ghcr.io/silva324/dali2mqtt:latest-arm64
  
# Check logs
docker logs -f dali2mqtt


## DEV MODE: 

```bash

cd dali/dali2mqtt
docker stop dali2mqtt && docker rm dali2mqtt

docker build -t ghcr.io/silva324/dali2mqtt:latest-arm64-local \
  --build-arg VERSION=local \
  -f Dockerfile .



  docker run -d \
  --name dali2mqtt \
  --privileged \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -v /dev:/dev \
  -v ./config:/app/config:rw \
  -v ./data:/app/data:rw \
  -v ./dali2mqtt:/app/dali2mqtt:rw \
  ghcr.io/silva324/dali2mqtt:latest-arm64

  docker restart dali2mqtt && docker logs -f dali2mqtt


```
