ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /app

# pytuya-pilot-wire holds everything that knows Tuya. It is not on PyPI yet: pinned here by tag
# of its repository (a tarball, so the image needs no git).
ARG PYTUYA_PILOT_WIRE_REF="v0.4.0"
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir \
       "pytuya-pilot-wire[sharing,lan,developer,mqtt] @ https://github.com/aghulas/pytuya-pilot-wire/archive/refs/tags/${PYTUYA_PILOT_WIRE_REF}.tar.gz" \
    && pip install --no-cache-dir /app \
    && rm -rf /app/src /app/pyproject.toml /app/build

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
