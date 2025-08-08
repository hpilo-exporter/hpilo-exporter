FROM python:3.13.3-alpine3.20

# Install psutil - needs linux-headers and build-base with gcc, remove it afterwards by name '.build-steps'. 
RUN apk --no-cache --virtual .build-steps add linux-headers build-base && pip install psutil && apk --no-cache del .build-steps

# Install exporter.
COPY . /usr/src/hpilo_exporter
RUN pip install -e /usr/src/hpilo_exporter

ENTRYPOINT ["hpilo-exporter"]
EXPOSE 9416

RUN addgroup -S hpilo_exporter && adduser -S hpilo_exporter -G hpilo_exporter
USER hpilo_exporter
# These warnings can be ignored:
# WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager.
# It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
# WARNING: You are using pip version 21.2.4; however, version 21.3.1 is available.
# You should consider upgrading via the '/usr/local/bin/python -m pip install --upgrade pip' command.
