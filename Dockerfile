FROM python:3.12.4-alpine3.20
ADD . /usr/src/hpilo_exporter

# Install psutil - needs linux-headers and build-base with gcc, remove it afterwards by name '.build-steps'. Install exporter.
RUN apk update && apk add --no-cache --virtual .build-steps linux-headers build-base && pip install psutil && pip install -e /usr/src/hpilo_exporter && apk del .build-steps

ENTRYPOINT ["hpilo-exporter"]
EXPOSE 9416

# These warnings can be ignored:
# WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager.
# It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
# WARNING: You are using pip version 21.2.4; however, version 21.3.1 is available.
# You should consider upgrading via the '/usr/local/bin/python -m pip install --upgrade pip' command.
