#!/bin/sh
set -e

# Buat symbolic link storage jika belum ada
php artisan storage:link || true

# Jalankan migrasi database
php artisan migrate --force || true

# Cache config, route, view untuk performa production
php artisan config:cache || true
php artisan route:cache || true
php artisan view:cache || true

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf

