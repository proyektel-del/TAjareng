#!/bin/sh
set -e

# Buat folder konfigurasi Nginx jika belum ada
mkdir -p /etc/nginx/templates

# Buat konfigurasi Nginx dengan port dinamis dari $PORT (default 80)
TARGET_PORT=${PORT:-80}
echo "[INFO] Configuring Nginx to listen on port: $TARGET_PORT"

cat <<EOF > /etc/nginx/http.d/default.conf
server {
    listen $TARGET_PORT;
    server_name _;
    root /var/www/public;
    index index.php index.html;

    client_max_body_size 32M;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript image/svg+xml;

    location / {
        try_files \$uri \$uri/ /index.php?\$query_string;
    }

    location ~* \.(?:css(\.map)?|js(\.map)?|jpe?g|png|gif|ico|cur|heic|webp|tiff?|mp3|m4a|aac|ogg|midi?|wav|mp4|mov|webm|mpe?g|avi|ogv|flv|wmv|svgz?|ttf|ttc|otf|eot|woff2?)$ {
        expires 1y;
        access_log off;
        add_header Cache-Control "public, immutable";
        try_files \$uri =404;
    }

    location ~ \.php$ {
        fastcgi_pass 127.0.0.1:9000;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME \$realpath_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 60;
        fastcgi_buffer_size 16k;
        fastcgi_buffers 4 16k;
    }

    location ~ /\.(?!well-known).* {
        deny all;
    }
}
EOF

# Pastikan permission folder storage dan bootstrap/cache aman
chmod -R 775 /var/www/storage /var/www/bootstrap/cache || true
chown -R www-data:www-data /var/www/storage /var/www/bootstrap/cache || true

# Storage link & database migrate
php artisan storage:link || true
php artisan migrate --force || true

# Caching Laravel
php artisan config:cache || true
php artisan route:cache || true
php artisan view:cache || true

echo "[INFO] Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
