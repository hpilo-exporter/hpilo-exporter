# HP iLO Metrics Exporter

Exporter for HP Server Integrated Lights Out (iLO) information to Prometheus.  
 - support for Python 3.6.  
 - ilo_user, ilo_password, ilo_port may be preset via environment.  
 - storage health information from iLO (cache, controller, logical drives, physical drives).  
 -  temperature values information from iLO.
 - per-fan and per-power-supply statuses.
 - OA info for Blade servers
 - Server ON status.
  

### Gauges

Here are the status code of gauge
```
-1 - Absent
 0 - OK
 1 - Degraded
 2 - Dead (Other)
```


### Output example

Example of status of your iLO
```
health_at_a_glance:
  battery: {status: OK}
  bios_hardware: {status: OK}
  fans: {redundancy: Redundant, status: OK}
  memory: {status: OK}
  network: {status: Link Down},
  power_supplies: {redundancy: Redundant, status: OK}
  processor: {status: OK}
  storage: {status: Degraded}
  temperature: {status: OK}
  vrm: {status: Ok}
  drive: {status: Ok}
```

The returned output would be:
```
hpilo_battery_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 1.0
hpilo_fans_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_bios_hardware_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_memory_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_power_supplies_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_processor_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_network_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 2.0
hpilo_temperature_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_vrm_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_drive_status{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_firmware_version{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 2.6
hpilo_storage_cache_health_status{controller="Controller on System Board, Smart Array P220i Controller",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 1.0
hpilo_storage_controller_health_status{controller="Controller on System Board, Smart Array P220i Controller",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_enclosure_health_status{controller="Controller on System Board, Smart Array P220i Controller",enc="0",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_ld_health_status{controller="Controller on System Board, Smart Array P220i Controller",logical_drive="LD_0, 279 GiB, RAID 1/RAID 1+0",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_pd_health_status{controller="Controller on System Board, Smart Array P220i Controller",logical_drive="LD_0, 279 GiB, RAID 1/RAID 1+0",physical_drive="EG0300FCSPH, 279 GiB, Port 1I Box 1 Bay 1",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_temperature_value{product_name="ProLiant BL460c Gen8",sensor="01-Inlet Ambient",server_name="name.fqdn.domain"} 19.0
hpilo_fan_status{fan="Fan 7",product_name="ProLiant DL360e Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_fan_speed{fan="Fan 7",product_name="ProLiant DL360e Gen8",server_name="name.fqdn.domain"} 29.0
hpilo_power_supply_status{product_name="ProLiant DL360e Gen8",ps="Power Supply 2",server_name="name.fqdn.domain"} 0.0
hpilo_running_status{product_name="ProLiant DL360e Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_onboard_administrator_info{encl="c7000name",location_bay="7",oa_ip="192.168.1.1",product_name="ProLiant BL460c Gen8",server_name="name2.fqdn.domain"} 0.0


```

### Installing

You can install exporter on the server directly or on separate machine.
To run, you must have `Python` and `pip` installed.

To install with `pip`:
```
pip install -e $HPILO_EXPORTER_DIR
```

Then just:
```
export ilo_user=user
export ilo_password=password
export ilo_port=443
hpilo-exporter [--address=0.0.0.0 --port=9416 --endpoint="/metrics"]
```

### Easy Install bash-script with systemd service (tested on ubuntu)
```
bash <(curl -Ls https://raw.githubusercontent.com/hpilo-exporter/hpilo-exporter/master/install.sh)
```


### Docker

To build the image yourself
```
docker build --rm -t hpilo-exporter .
```

To run the container
```
docker run -p 9416:9416 hpilo-exporter:latest
```

You can then call the web server on the defined endpoint, `/metrics` by default.
```
curl 'http://127.0.0.1:9416/metrics?ilo_host=1.1.1.1&ilo_port=443&ilo_user=admin&ilo_password=admin'
```
or
```
curl 'http://127.0.0.1:9416/metrics?ilo_host=1.1.1.1'
```

Passing argument to the docker run command
```
docker run -p 9416:9416 hpilo-exporter:latest --port 9416 --ilo_user my_user --ilo_password my_secret_password
```

### Docker compose

The easiest way to run this exporter in docker compose is to get compose to pull the latest version directly from github and build it:

```yml
services:
  hpilo-exporter:
    build:
      context: https://github.com/hpilo-exporter/hpilo-exporter.git#main
    ports:
      - 9416:9416
```

Here is a more elaborate example that pulls the docker image from a registry, passes additional arguments to the exporter executable, and uses placement group constraints for compose swarm mode:

```yml
version: '3'
services:
  hpilo-exporter:
    image: my.registry/hpilo-exporter
    ports:
      - 9416:9416
    command: ['--port=9416','--endpoint=/metrics','--address=0.0.0.0']
    deploy:
      placement:
        constraints:
          - node.hostname == my_node.domain
```

### Kubernetes

A helm chart is available at [prometheus-helm-addons](https://github.com/IDNT/prometheus-helm-addons).

### Prometheus config

Assuming:
- the exporter is available on `http://127.0.0.1:9416`
- you use same the port,username and password for all your iLO
- change 127.0.0.1 address to actual address of exporter if prometheus is running on different host  

```yml
- job_name: 'hpilo'
  scrape_interval: 1m
  scrape_timeout: 30s
  params: 
    ilo_host: ['']
    #ilo_port: ['443']                 # may be set in exporter ENV
    #ilo_user: ['my_ilo_user']         # may be set in exporter ENV
    #ilo_password: ['my_ilo_password'] # may be set in exporter ENV
  static_configs:
    - targets:
      - ilo_fqdn.domain

  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_ilo_host
    - source_labels: [__param_ilo_host]
      target_label: ilo_host
    - target_label: __address__
      replacement: 127.0.0.1:9416  # hpilo exporter.
```

