#!/bin/bash

set -euo pipefail

# Corporate/endpoint TLS scanners may add a local CA that is not present in
# the slim Linux image. When explicitly mounted, merge it into the system
# bundle while keeping certificate verification enabled.
if [[ -n "${ARELBOT_EXTRA_CA_CERT:-}" && -f "${ARELBOT_EXTRA_CA_CERT}" ]]; then
    extra_ca_pem="/tmp/arelbot-extra-ca.pem"
    if openssl x509 -inform PEM -in "${ARELBOT_EXTRA_CA_CERT}" -noout >/dev/null 2>&1; then
        cp "${ARELBOT_EXTRA_CA_CERT}" "${extra_ca_pem}"
    elif openssl x509 -inform DER -in "${ARELBOT_EXTRA_CA_CERT}" -noout >/dev/null 2>&1; then
        openssl x509 -inform DER -in "${ARELBOT_EXTRA_CA_CERT}" -out "${extra_ca_pem}"
    else
        echo "ARELBOT_EXTRA_CA_CERT geÃ§erli bir PEM/DER sertifikasÄ± deÄŸil; sistem CA zinciri kullanÄ±lacak." >&2
        extra_ca_pem=""
    fi
    if [[ -n "${extra_ca_pem}" ]]; then
        cat /etc/ssl/certs/ca-certificates.crt "${extra_ca_pem}" > /tmp/arelbot-ca-bundle.pem
        export SSL_CERT_FILE=/tmp/arelbot-ca-bundle.pem
        export REQUESTS_CA_BUNDLE=/tmp/arelbot-ca-bundle.pem
        export PIP_CERT=/tmp/arelbot-ca-bundle.pem
    fi
fi

exec python main.py
