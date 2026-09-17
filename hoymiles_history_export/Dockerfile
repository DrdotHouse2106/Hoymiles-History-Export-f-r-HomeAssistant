ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip

RUN pip3 install --no-cache-dir --break-system-packages \
    requests==2.32.3 \
    websockets==13.1 \
    argon2-cffi==23.1.0

COPY run.sh /run.sh
COPY backfill.py /backfill.py
COPY hoymiles_client.py /hoymiles_client.py
COPY protobuf_mini.py /protobuf_mini.py

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
