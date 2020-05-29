# HP iLO Metrics Exporter

Blackbox likes exporter used to exports HP Server Integrated Lights Out (iLO) states to Prometheus.  
Added support for Python 3.6.  
ilo_user, ilo_password, ilo_port may be preset via environment.  
Added storage health information from iLO (cache, controller, logical drives, physical drives).  
Added temperature values information from iLO.  

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
hpilo_battery{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 1.0
hpilo_fans{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_bios_hardware{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_memory{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_power_supplies{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_processor{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_network{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 2.0
hpilo_temperature{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_vrm{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_drive{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_firmware_version{product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 2.5
hpilo_storage_cache_health{controller="Controller on System Board, Smart Array P220i Controller",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 1.0
hpilo_storage_controller_health{controller="Controller on System Board, Smart Array P220i Controller",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_enclosure_health{controller="Controller on System Board, Smart Array P220i Controller",enc="0",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_ld_health{controller="Controller on System Board, Smart Array P220i Controller",logical_drive="LD_0, 279 GiB, RAID 1/RAID 1+0",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_pd_health{controller="Controller on System Board, Smart Array P220i Controller",logical_drive="LD_0, 279 GiB, RAID 1/RAID 1+0",physical_drive="EG0300FCSPH, 279 GiB, Port 1I Box 1 Bay 1",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
hpilo_storage_pd_health{controller="Controller on System Board, Smart Array P220i Controller",logical_drive="LD_0, 279 GiB, RAID 1/RAID 1+0",physical_drive="EG0300FCSPH, 279 GiB, Port 1I Box 1 Bay 2",product_name="ProLiant BL460c Gen8",server_name="name.fqdn.domain"} 0.0
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

Here is an example of Docker Compose deployment:

```yml
hpilo:
    image: my.registry/hpilo-exporter
    ports:
      - 9416:9416
    command:
      - '--port=9416'
    deploy:
      placement:
        constraints:
          - node.hostname == my_node.domain
```

### Kubernetes

A helm chart is available at [prometheus-helm-addons](https://github.com/IDNT/prometheus-helm-addons).

### Prometheus config

Assuming:
- the exporter is available on `http://hpilo:9416`
- you use same the port,username and password for all your iLO

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
      replacement: hpilo:8082  # hpilo exporter.
```

