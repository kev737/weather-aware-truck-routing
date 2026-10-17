#!/bin/bash
# EC2 boot script (Amazon Linux 2023, t4g.micro): Docker + the app + Caddy for HTTPS.
# The site name is the Elastic IP on sslip.io, e.g. 52-24-82-112.sslip.io.
set -euxo pipefail
SITE="${SITE:?set SITE}"
REPO=https://github.com/kev737/weather-aware-truck-routing.git

# 2 GB swap: the Vite build needs more than the micro's 1 GB of RAM.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

dnf install -y docker git
systemctl enable --now docker

[ -d /opt/app ] || git clone "$REPO" /opt/app
mkdir -p /opt/caddy
cat > /opt/caddy/Caddyfile <<CADDY
$SITE {
	encode zstd gzip
	reverse_proxy app:8000
}
CADDY
[ -f /opt/app.env ] || printf 'DJANGO_SECRET_KEY=%s\nDJANGO_ALLOWED_HOSTS=%s,localhost\n' "$(openssl rand -hex 32)" "$SITE" > /opt/app.env
chmod 600 /opt/app.env

# /opt/deploy.sh pulls main and runs the versioned script in the repo.
cat > /opt/deploy.sh <<'DEPLOY'
#!/bin/bash
set -euo pipefail
cd /opt/app && git fetch -q origin && git reset -q --hard origin/main
exec /opt/app/deploy/ec2/deploy.sh
DEPLOY
chmod +x /opt/deploy.sh
/opt/deploy.sh
