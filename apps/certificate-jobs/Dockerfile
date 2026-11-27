FROM ubuntu:24.04@sha256:9a04f13b3f80ef056361e30868593b1cfd2279fe19e4081cb93a90bf56819d6a
ENV PYTHONDONTWRITEBYTECODE=1 POWERSHELL_TELEMETRY_OPTOUT=1
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl python3 unzip \
    && curl --proto '=https' --tlsv1.2 -fsSL --retry 3 https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb -o /tmp/packages-microsoft-prod.deb \
    && echo 'c13f01ac7c3001b51a9281d40dde666db5e037e05512840c319832f7852bfec4  /tmp/packages-microsoft-prod.deb' | sha256sum -c - \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends powershell=7.6.7-1.deb \
    && gallery="$(pwsh -NoLogo -NoProfile -NonInteractive -Command '$ErrorActionPreference="Stop"; if (-not (Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue)) { Register-PSRepository -Default }; (Get-PSRepository -Name PSGallery).SourceLocation.TrimEnd("/")')" \
    && curl --proto '=https' --tlsv1.2 -fsSL --retry 3 "$gallery/package/Posh-ACME/4.34.0" -o /tmp/posh-acme.nupkg \
    && echo '692839052d3a4d524f53a0ab6d9c39fe7a710faa92e079dfcdb643fce409e041  /tmp/posh-acme.nupkg' | sha256sum -c - \
    && install -d /usr/local/share/powershell/Modules/Posh-ACME/4.34.0 \
    && unzip -q /tmp/posh-acme.nupkg -d /usr/local/share/powershell/Modules/Posh-ACME/4.34.0 \
    && rm -rf /var/lib/apt/lists/* /tmp/packages-microsoft-prod.deb /tmp/posh-acme.nupkg \
    && useradd --uid 10001 --home-dir /var/lib/certificate-job --create-home --shell /usr/sbin/nologin certificate \
    && chmod 0700 /var/lib/certificate-job
WORKDIR /app
COPY job.py certificate-job.ps1 certificate.ps1 portal-auth-certificate.ps1 staging-ca.crt ./
RUN chmod 0444 /app/*.ps1 /app/job.py /app/staging-ca.crt
USER certificate
ENV HOME=/var/lib/certificate-job
ENTRYPOINT ["/usr/bin/python3", "/app/job.py"]
