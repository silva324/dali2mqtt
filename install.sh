#!/bin/bash

# DALI2MQTT Installation Script - Ubuntu/Debian Only
# This script automates the installation of DALI2MQTT

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
INSTALL_DIR="/opt/dali2mqtt"
SERVICE_USER="dali2mqtt"
INSTALL_TYPE="docker"

# Functions
print_banner() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════╗"
    echo "║          DALI2MQTT Installer         ║"
    echo "║        Ubuntu/Debian Support         ║"
    echo "╚══════════════════════════════════════╝"
    echo -e "${NC}"
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_os() {
    if ! command -v apt-get &> /dev/null; then
        log_error "This script only supports Ubuntu/Debian systems"
        exit 1
    fi
    
    log_info "Detected Ubuntu/Debian system"
}

install_dependencies() {
    log_info "Updating package list..."
    apt-get update

    log_info "Installing system dependencies..."
    apt-get install -y \
        curl \
        wget \
        git \
        python3 \
        python3-pip \
        python3-venv \
        gcc \
        libc6-dev \
        libusb-1.0-0-dev \
        libudev-dev \
        pkg-config \
        udev \
        netcat-openbsd
}

install_docker() {
    if ! command -v docker &> /dev/null; then
        log_info "Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
        rm get-docker.sh
        systemctl enable docker
        systemctl start docker
    else
        log_info "Docker is already installed"
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        log_info "Installing Docker Compose..."
        curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
    else
        log_info "Docker Compose is already installed"
    fi
}

create_user() {
    if ! id "$SERVICE_USER" &>/dev/null; then
        log_info "Creating user $SERVICE_USER..."
        useradd -r -s /bin/false -d $INSTALL_DIR $SERVICE_USER
        usermod -a -G plugdev,dialout $SERVICE_USER
    else
        log_info "User $SERVICE_USER already exists"
    fi
}

setup_udev_rules() {
    log_info "Setting up USB device permissions..."
    
    cat > /etc/udev/rules.d/50-dali2mqtt.rules << 'EOF'
# HASSEB DALI USB interface
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="04d8", ATTRS{idProduct}=="f2f7", MODE="0660", GROUP="plugdev", SYMLINK+="dali/hasseb-%n"

# Tridonic DALI USB interface  
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="17b5", ATTRS{idProduct}=="0020", MODE="0660", GROUP="plugdev", SYMLINK+="dali/daliusb-%n"
EOF
    
    udevadm control --reload-rules
    udevadm trigger
}

setup_docker_installation() {
    log_info "Setting up Docker installation..."
    
    # Create directories
    mkdir -p $INSTALL_DIR/{config,data}
    
    # Copy project files
    cp -r . $INSTALL_DIR/
    cd $INSTALL_DIR
    
    # Create default configuration
    if [ ! -f config/config.yaml ]; then
        log_info "Creating default configuration..."
        mkdir -p config
        cat > config/config.yaml << 'EOF'
mqtt_server: localhost
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_base_topic: dali2mqtt
dali_driver: hid_hasseb
ha_discovery_prefix: homeassistant
log_level: info
log_color: false
devices_names_file: /app/data/devices.yaml
EOF
    fi

    # Create docker-compose.yml without local MQTT broker
    if [ ! -f docker-compose.yml ]; then
        log_info "Creating Docker Compose configuration..."
        cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  dali2mqtt:
    build: .
    container_name: dali2mqtt
    restart: unless-stopped
    privileged: true
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - /dev:/dev
    environment:
      - MQTT_SERVER=${MQTT_SERVER:-localhost}
      - MQTT_PORT=${MQTT_PORT:-1883}
      - MQTT_USERNAME=${MQTT_USERNAME:-}
      - MQTT_PASSWORD=${MQTT_PASSWORD:-}
    network_mode: host
EOF
    fi

    # Create environment file for easy configuration
    if [ ! -f .env ]; then
        log_info "Creating environment configuration file..."
        cat > .env << 'EOF'
# MQTT Broker Configuration
# Edit these values to match your MQTT broker settings
MQTT_SERVER=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=

# Optional: Override other settings
# MQTT_BASE_TOPIC=dali2mqtt
# DALI_DRIVER=hid_hasseb
# LOG_LEVEL=info
EOF
    fi
    
    # Set proper ownership
    chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR
    
    # Build Docker image
    log_info "Building Docker image..."
    docker-compose build
    
    log_info "Docker installation complete!"
    log_warn "Please edit $INSTALL_DIR/.env with your MQTT broker settings before starting"
}

setup_native_installation() {
    log_info "Setting up native installation..."
    
    # Create directories
    mkdir -p $INSTALL_DIR
    cp -r . $INSTALL_DIR/
    cd $INSTALL_DIR
    
    # Create virtual environment
    log_info "Creating Python virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    
    # Install Python dependencies
    log_info "Installing Python dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    
    # Create default configuration
    if [ ! -f config.yaml ]; then
        log_info "Creating default configuration..."
        cat > config.yaml << 'EOF'
mqtt_server: localhost
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_base_topic: dali2mqtt
dali_driver: hid_hasseb
ha_discovery_prefix: homeassistant
log_level: info
log_color: false
devices_names_file: devices.yaml
EOF
    fi
    
    # Create systemd service
    log_info "Creating systemd service..."
    cat > /etc/systemd/system/dali2mqtt.service << EOF
[Unit]
Description=DALI2MQTT Bridge
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$INSTALL_DIR/venv/bin/python -m dali2mqtt.dali2mqtt --config $INSTALL_DIR/config.yaml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    # Set proper ownership
    chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR
    
    # Enable service (don't start yet)
    systemctl daemon-reload
    systemctl enable dali2mqtt.service
    
    log_info "Native installation complete!"
    log_warn "Please edit $INSTALL_DIR/config.yaml with your MQTT broker settings before starting"
}

show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "DALI2MQTT Installation Script for Ubuntu/Debian"
    echo
    echo "Options:"
    echo "  -t, --type TYPE     Installation type: docker, native (default: docker)"
    echo "  -u, --user USER     Service user (default: dali2mqtt)"
    echo "  -h, --help          Show this help"
    echo
    echo "Examples:"
    echo "  sudo $0                    # Docker installation (recommended)"
    echo "  sudo $0 -t native          # Native installation"
    echo "  sudo $0 -u myuser          # Custom service user"
}

show_completion_message() {
    echo
    echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║          Installation Complete!      ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
    echo
    
    if [[ "$INSTALL_TYPE" == "docker" ]]; then
        echo -e "${BLUE}Docker Installation:${NC}"
        echo "  Location: $INSTALL_DIR"
        echo
        echo -e "${YELLOW}⚠️  IMPORTANT: Configure MQTT broker settings${NC}"
        echo "  Edit: $INSTALL_DIR/.env"
        echo "  Set your MQTT broker IP address and credentials"
        echo
        echo -e "${BLUE}After configuration:${NC}"
        echo "  Start:    docker-compose -f $INSTALL_DIR/docker-compose.yml up -d"
        echo "  Status:   docker-compose -f $INSTALL_DIR/docker-compose.yml ps"
        echo "  Logs:     docker-compose -f $INSTALL_DIR/docker-compose.yml logs -f"
        echo "  Stop:     docker-compose -f $INSTALL_DIR/docker-compose.yml down"
    else
        echo -e "${BLUE}Native Installation:${NC}"
        echo "  Location: $INSTALL_DIR"
        echo
        echo -e "${YELLOW}⚠️  IMPORTANT: Configure MQTT broker settings${NC}"
        echo "  Edit: $INSTALL_DIR/config.yaml"
        echo "  Set your MQTT broker IP address and credentials"
        echo
        echo -e "${BLUE}After configuration:${NC}"
        echo "  Start:    sudo systemctl start dali2mqtt"
        echo "  Status:   sudo systemctl status dali2mqtt"
        echo "  Logs:     sudo journalctl -u dali2mqtt -f"
        echo "  Stop:     sudo systemctl stop dali2mqtt"
    fi
    
    echo
    echo -e "${BLUE}Configuration Files:${NC}"
    if [[ "$INSTALL_TYPE" == "docker" ]]; then
        echo "  MQTT settings: $INSTALL_DIR/.env"
        echo "  Main config:   $INSTALL_DIR/config/config.yaml"
    else
        echo "  Main config:   $INSTALL_DIR/config.yaml"
    fi
    echo "  Device names:  $INSTALL_DIR/data/devices.yaml"
    echo
    echo -e "${BLUE}Next Steps:${NC}"
    echo "  1. Connect your DALI USB device"
    echo "  2. Configure MQTT broker settings (see above)"
    echo "  3. Start the service"
    echo "  4. Check logs for any issues"
    echo
    echo -e "${BLUE}Example MQTT configuration:${NC}"
    echo "  MQTT_SERVER=192.168.1.100"
    echo "  MQTT_PORT=1883"
    echo "  MQTT_USERNAME=homeassistant"
    echo "  MQTT_PASSWORD=your_password"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            INSTALL_TYPE="$2"
            if [[ "$INSTALL_TYPE" != "docker" && "$INSTALL_TYPE" != "native" ]]; then
                log_error "Invalid installation type. Use 'docker' or 'native'"
                exit 1
            fi
            shift 2
            ;;
        -u|--user)
            SERVICE_USER="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Main installation process
main() {
    print_banner
    
    check_root
    check_os
    
    log_info "Starting DALI2MQTT installation..."
    log_info "Installation type: $INSTALL_TYPE"
    log_info "Installation directory: $INSTALL_DIR"
    log_info "Service user: $SERVICE_USER"
    echo
    
    install_dependencies
    setup_udev_rules
    create_user
    
    if [[ "$INSTALL_TYPE" == "docker" ]]; then
        install_docker
        setup_docker_installation
    else
        setup_native_installation
    fi
    
    show_completion_message
}

# Run main function
main "$@"