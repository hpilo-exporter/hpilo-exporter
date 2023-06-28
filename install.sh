#!/bin/bash

#install pip
echo "############## Installing python3-pip and psutil ##################"
apt update && apt install -y python3-pip
pip install psutil

echo "############## Installing hpilo-exporter ##################"
#git clone and install
git clone https://github.com/hpilo-exporter/hpilo-exporter
cd hpilo-exporter && pip install .

echo "############## Create User for hpilo-exporter ##################"
# create user
useradd --no-create-home --shell /bin/false hpilo-exporter

echo "############## Creating Systemd Service ##################"
echo '[Unit]
Description=hpilo Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=hpilo-exporter
Group=hpilo-exporter
Type=simple
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/local/bin/hpilo-exporter  \
--address=0.0.0.0 \
--port=9416  \
--endpoint=/metrics

[Install]
WantedBy=multi-user.target' > /etc/systemd/system/hpilo-exporter.service

# enable haproxy_exporter in systemctl
echo "############## systemctl daemon-reload ##################"
systemctl daemon-reload
echo "############## Start and Enable hpilo-exporter Service ##################"
systemctl start hpilo-exporter
systemctl enable hpilo-exporter

echo "service hpilo-exporter status :"
service hpilo-exporter status

echo "############## SETUP COMPLETED ##################"
echo "Setup complete.
Add the following lines to /etc/prometheus/prometheus.yml:

  - job_name: 'hpilo-exporter'
  scrape_interval: 1m
  scrape_timeout: 30s
  params:
    ilo_port: ['443']
    ilo_user: ['my_ilo_user']
    ilo_password: ['my_ilo_password']
  static_configs:
    - targets:
      - ilo_fqdn.domain

  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_ilo_host
    - source_labels: [__param_ilo_host]
      target_label: ilo_host
    - target_label: __address__
      replacement: localhost:9416  # hpilo exporter.



"
