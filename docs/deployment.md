# Deployment Guide

Deploy the Inky Display Controller on a Raspberry Pi with an Inky Impression e-ink display.

## Prerequisites

- Raspberry Pi with Debian/Raspberry Pi OS (aarch64)
- Python 3.12+
- Inky Impression display connected via SPI/I2C
- User with access to `i2c`, `spi`, and `gpio` groups
- Network access to MQTT broker

## Installation

### 1. Install uv Package Manager

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
```

### 2. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3-dev
```

### 3. Create Installation Directory

```bash
sudo mkdir -p /opt/inky-controller
sudo chown $USER:$USER /opt/inky-controller
```

### 4. Create Virtual Environment and Install Package

```bash
cd /opt/inky-controller
uv venv --python 3.13
uv pip install 'private-assistant-display-controller[rpi]'
```

### 5 Activate I2C
Check if I2C is enabled:

```bash
sudo raspi-config
# Interface Options → I2C → Enable
# Interface Options → SPI → Enable
```


## Configuration

Create `/opt/inky-controller/config.yaml`:

```yaml
device:
  id: "my-device-id"
  room: "living-room"

mqtt:
  host: "mqtt.example.com"
  port: 443
  username: "device-user"
  password: "your-password"
  transport: "websockets"
  websocket_path: "/mqtt"
  tls: true

display:
  orientation: "landscape"
  saturation: 0.5
  mock: false
```

### Configuration Reference

| Section | Field | Description | Default |
|---------|-------|-------------|---------|
| device | id | Unique device identifier | inky-display |
| device | room | Room location | null |
| mqtt | host | MQTT broker hostname | localhost |
| mqtt | port | MQTT broker port | 1883 |
| mqtt | username | MQTT username | null |
| mqtt | password | MQTT password | null |
| mqtt | transport | tcp or websockets | tcp |
| mqtt | websocket_path | WebSocket path | null |
| mqtt | tls | Enable TLS | false |
| display | orientation | landscape or portrait | landscape |
| display | saturation | Color saturation (0.0-1.0) | 0.5 |
| display | mock | Use mock display | false |

## systemd Service

Create `/etc/systemd/system/inky-controller.service`:

```ini
[Unit]
Description=Inky Display Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/inky-controller
ExecStart=/opt/inky-controller/.venv/bin/inky-controller --config /opt/inky-controller/config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable inky-controller
sudo systemctl start inky-controller
```

## Troubleshooting

### Check Service Status

```bash
sudo systemctl status inky-controller
```

### View Logs

```bash
# Recent logs
sudo journalctl -u inky-controller -n 50

# Follow logs in real-time
sudo journalctl -u inky-controller -f
```

### Restart Service

```bash
sudo systemctl restart inky-controller
```

### Test CLI Manually

```bash
/opt/inky-controller/.venv/bin/inky-controller --config /opt/inky-controller/config.yaml --verbose
```

### Common Issues

**SPI/I2C Permission Denied**

Ensure user is in required groups:
```bash
sudo usermod -a -G i2c,spi,gpio $USER
```

**MQTT Connection Failed**

- Verify broker hostname and port
- Check TLS settings match broker configuration
- Ensure credentials are correct

**Display Not Detected**

- Check SPI is enabled: `ls /dev/spidev*`
- Check I2C is enabled: `ls /dev/i2c*`
- Verify display cable connections
