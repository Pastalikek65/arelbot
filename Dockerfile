FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    bash \
    ca-certificates \
    openssl \
    ffmpeg \
    aria2 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# pip/Requests normally prefer certifi.  Point them at the system bundle so
# an optional corporate/endpoint inspection root mounted as a BuildKit secret
# is honored without disabling TLS verification or using trusted-host.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# RE-DEPLOY TRIGGER: 2026-01-24-1358
WORKDIR /app

# Copy requirements and install
COPY requirements.txt constraints.txt .
# Windows/Docker Desktop development can opt into trusted PyPI hosts when a
# local TLS inspector replaces the public CA. Production builds leave this
# empty and use the normal system certificate chain.
ARG PIP_TRUSTED_HOSTS=""
RUN --mount=type=secret,id=arelbot_ca,target=/tmp/arelbot-ca.crt,required=false \
    set -eu; \
    if openssl x509 -inform PEM -in /tmp/arelbot-ca.crt -noout >/dev/null 2>&1; then \
      cp /tmp/arelbot-ca.crt /usr/local/share/ca-certificates/arelbot-extra-ca.crt; \
      update-ca-certificates; \
    elif openssl x509 -inform DER -in /tmp/arelbot-ca.crt -noout >/dev/null 2>&1; then \
      openssl x509 -inform DER -in /tmp/arelbot-ca.crt -out /usr/local/share/ca-certificates/arelbot-extra-ca.crt; \
      update-ca-certificates; \
    fi; \
    if [ -n "$PIP_TRUSTED_HOSTS" ]; then \
      pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -c constraints.txt -r requirements.txt; \
    else \
      pip install --no-cache-dir -c constraints.txt -r requirements.txt; \
    fi

# MCP is an optional integration. The core image stays independent from the
# external MCP server ecosystem; build it explicitly with INSTALL_MCP=true.
ARG INSTALL_MCP=false
COPY requirements-optional.txt .
RUN if [ "$INSTALL_MCP" = "true" ]; then \
      if [ -n "$PIP_TRUSTED_HOSTS" ]; then \
        pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -c constraints.txt -r requirements-optional.txt; \
      else \
        pip install --no-cache-dir -c constraints.txt -r requirements-optional.txt; \
      fi; \
    fi

# Copy source code
COPY . .

# Copy and make entrypoint executable
COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh \
    && chmod 600 deploy/evidence/provider-approval-manifest.json

CMD ["./entrypoint.sh"]
