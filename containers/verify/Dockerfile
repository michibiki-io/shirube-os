ARG BASE=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
FROM ${BASE}
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm main' > /etc/apt/sources.list && \
    apt-get -o APT::Update::Error-Mode=any update && apt-get install -y --no-install-recommends ca-certificates python3 minisign && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY producer /src/producer
COPY source-lock.json /src/source-lock.json
ENTRYPOINT ["python3", "-m", "producer.cli"]
