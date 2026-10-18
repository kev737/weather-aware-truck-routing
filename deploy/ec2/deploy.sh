#!/bin/bash
# Rebuild and restart the app on the EC2 host (run by /opt/deploy.sh after a git pull).
# Secrets never live in git: the Open-Meteo key is read from SSM Parameter Store.
set -euo pipefail
PARAM=/truck-routing/open-meteo-api-key
REGION=$(TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60") \
  && curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

# Refresh the key line in the env file (empty if the parameter doesn't exist: free tier).
KEY=$(aws ssm get-parameter --region "$REGION" --name "$PARAM" --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)
grep -v '^OPEN_METEO_API_KEY=' /opt/app.env > /opt/app.env.new || true
[ -n "$KEY" ] && echo "OPEN_METEO_API_KEY=$KEY" >> /opt/app.env.new
mv /opt/app.env.new /opt/app.env && chmod 600 /opt/app.env
echo "weather tier: $([ -n "$KEY" ] && echo paid || echo free)"

docker build -t truck-routing /opt/app
docker network inspect web >/dev/null 2>&1 || docker network create web
docker rm -f app >/dev/null 2>&1 || true
# The cache volume keeps forecasts, trips and the weather-quota count across deploys.
CACHE_DIR=/tmp/truck-routing-cache
# Logs are capped (3 x 10 MB) so the access log can't fill the disk.
docker run -d --name app --network web --restart unless-stopped --env-file /opt/app.env \
  --log-opt max-size=10m --log-opt max-file=3 \
  -e CACHE_DIR=$CACHE_DIR -v truck_cache:$CACHE_DIR truck-routing
# Caddy: HTTPS for the app, plus /walkthrough (the video page; the video itself is copied to
# /opt/walkthrough by hand and never lives in git).
SITE=$(head -1 /opt/caddy/Caddyfile | cut -d' ' -f1)
mkdir -p /opt/walkthrough
cp /opt/app/deploy/ec2/walkthrough/index.html /opt/walkthrough/index.html
cat > /opt/caddy/Caddyfile <<CADDY
$SITE {
	encode zstd gzip
	handle_path /walkthrough* {
		root * /srv/walkthrough
		file_server
	}
	handle {
		reverse_proxy app:8000
	}
}
CADDY
if ! docker inspect caddy --format '{{range .Mounts}}{{.Destination}} {{end}}' 2>/dev/null | grep -q /srv/walkthrough; then
  docker rm -f caddy >/dev/null 2>&1 || true
  docker run -d --name caddy --network web --restart unless-stopped -p 80:80 -p 443:443 \
    --log-opt max-size=10m --log-opt max-file=3 \
    -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile:ro -v /opt/walkthrough:/srv/walkthrough:ro \
    -v caddy_data:/data -v caddy_config:/config caddy:2.10-alpine
else
  docker exec caddy caddy reload --config /etc/caddy/Caddyfile
fi
# Forecast grids (HRRR + GFS): refreshed hourly inside the app container, and right after a deploy.
cat > /etc/systemd/system/truck-grids.service <<'UNIT'
[Unit]
Description=Refresh NOAA forecast grids for the truck-routing app
[Service]
Type=oneshot
# Background priority: a reviewer's request always comes before decoding forecasts.
ExecStart=/usr/bin/docker exec app nice -n 19 python manage.py refresh_grids
UNIT
cat > /etc/systemd/system/truck-grids.timer <<'UNIT'
[Unit]
Description=Hourly NOAA grid refresh
[Timer]
OnCalendar=*-*-* *:15:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now truck-grids.timer
systemctl start --no-block truck-grids.service

docker image prune -f
