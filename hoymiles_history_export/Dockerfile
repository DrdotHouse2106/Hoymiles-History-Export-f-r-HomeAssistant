ARG BUILD_FROM=homeassistant/amd64-base-python:3.10-alpine3.18
FROM $BUILD_FROM

ENV LANG=C.UTF-8

RUN python -m pip install --upgrade pip
RUN pip3 install requests
RUN pip3 install websockets
RUN pip3 install argon2-cffi
RUN pip3 install paho-mqtt

COPY run.sh /
COPY backfill.py /
COPY hoymiles_client.py /
COPY protobuf_mini.py /
COPY live_gateway.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
