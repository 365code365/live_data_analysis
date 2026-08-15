# 出口代理网关：安卓容器共享本容器的 network namespace，
# 所有流量（含 UDP / DNS）经 tun0 → tun2socks → 上游代理出网。
FROM alpine:3.20

ARG TARGETARCH
ARG TUN2SOCKS_VERSION=v2.7.0
ARG DNSPROXY_VERSION=v0.84.0

RUN apk add --no-cache \
        bash curl ca-certificates iptables ip6tables iproute2 \
        bind-tools jq tzdata unzip \
    && rm -rf /var/cache/apk/*

# 架构探测：优先用 buildkit 提供的 TARGETARCH，缺失时回落到 uname
RUN set -eux; \
    arch="${TARGETARCH:-}"; \
    if [ -z "$arch" ]; then \
      case "$(uname -m)" in \
        x86_64) arch=amd64 ;; \
        aarch64|arm64) arch=arm64 ;; \
        *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;; \
      esac; \
    fi; \
    echo "building for linux/$arch"; \
    curl -fsSL -o /tmp/tun2socks.zip \
      "https://github.com/xjasonlyu/tun2socks/releases/download/${TUN2SOCKS_VERSION}/tun2socks-linux-${arch}.zip"; \
    unzip -o /tmp/tun2socks.zip -d /tmp/t2s; \
    mv "/tmp/t2s/tun2socks-linux-${arch}" /usr/local/bin/tun2socks; \
    chmod +x /usr/local/bin/tun2socks; \
    curl -fsSL -o /tmp/dnsproxy.tar.gz \
      "https://github.com/AdguardTeam/dnsproxy/releases/download/${DNSPROXY_VERSION}/dnsproxy-linux-${arch}-${DNSPROXY_VERSION}.tar.gz"; \
    tar -xzf /tmp/dnsproxy.tar.gz -C /tmp; \
    mv "/tmp/linux-${arch}/dnsproxy" /usr/local/bin/dnsproxy; \
    chmod +x /usr/local/bin/dnsproxy; \
    rm -rf /tmp/*; \
    tun2socks --version; dnsproxy --version || true

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY egress-ip.sh /usr/local/bin/egress-ip
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/egress-ip

ENV PROXY_URL="" \
    DNS_UPSTREAM="https://1.1.1.1/dns-query" \
    DNS_BOOTSTRAP="1.1.1.1:53,8.8.8.8:53" \
    TUN_NAME="tun0" \
    TUN_ADDR="198.18.0.1" \
    TUN_MASK="15" \
    TUN_MTU="1500" \
    KILL_SWITCH="true" \
    DISABLE_IPV6="true" \
    LOGLEVEL="info"

HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=3 \
    CMD /usr/local/bin/egress-ip >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
