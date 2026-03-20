#!/bin/bash
set -e

INSTALL_DIR="/opt/gpu-fan-controller"
SERVICE_NAME="gpu-fan-controller"

echo "Installing GPU Fan Controller..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp gpu_fan_controller.py "$INSTALL_DIR/"
sudo chmod +x "$INSTALL_DIR/gpu_fan_controller.py"
sudo cp gpu-fan-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "Installation complete!"
echo ""
echo "Commands:"
echo "  Start:   sudo systemctl start $SERVICE_NAME"
echo "  Stop:    sudo systemctl stop $SERVICE_NAME"
echo "  Status:  sudo systemctl status $SERVICE_NAME"
echo "  Logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "           tail -f /var/log/gpu-fan-controller.log"
echo ""
echo "To modify settings, edit: /etc/systemd/system/gpu-fan-controller.service"
echo "Then run: sudo systemctl daemon-reload && sudo systemctl restart $SERVICE_NAME"