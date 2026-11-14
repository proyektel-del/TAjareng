# ==========================================
# STAGE 1: Build Assets (Node)
# ==========================================
FROM node:22-alpine AS node-builder
WORKDIR /app

COPY package*.json ./
COPY .npmrc* ./
RUN npm install --ignore-scripts

COPY resources ./resources
COPY vite.config.js ./
COPY tailwind.config.js ./
COPY postcss.config.js* ./
RUN npm run build

# ==========================================
# STAGE 2: PHP Application (Production)
# ==========================================
FROM php:8.3-fpm-alpine

# Install minimal system packages for production
RUN apk add --no-cache \
    nginx \
    supervisor \
    curl \
    libpng \
    libpng-dev \
    libjpeg-turbo \
    libjpeg-turbo-dev \
    freetype \
    freetype-dev \
    libzip \
    libzip-dev \
    icu \
    icu-dev \
    oniguruma \
    oniguruma-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j$(nproc) pdo pdo_mysql mbstring zip exif pcntl bcmath gd intl opcache \
    && apk del --no-cache libpng-dev libjpeg-turbo-dev freetype-dev libzip-dev icu-dev oniguruma-dev

# OPcache settings for high performance and low CPU
RUN { \
    echo 'opcache.enable=1'; \
    echo 'opcache.memory_consumption=64'; \
    echo 'opcache.interned_strings_buffer=8'; \
    echo 'opcache.max_accelerated_files=5000'; \
    echo 'opcache.revalidate_freq=0'; \
    echo 'opcache.validate_timestamps=0'; \
    echo 'opcache.save_comments=1'; \
    echo 'opcache.fast_shutdown=1'; \
} > /usr/local/etc/php/conf.d/opcache-recommended.ini

# PHP memory & upload tuning (hemat resource RAM)
RUN { \
    echo 'memory_limit=128M'; \
    echo 'upload_max_filesize=16M'; \
    echo 'post_max_size=16M'; \
    echo 'max_execution_time=30'; \
    echo 'expose_php=off'; \
} > /usr/local/etc/php/conf.d/custom-php.ini

# Install Composer
COPY --from=composer:latest /usr/bin/composer /usr/bin/composer

WORKDIR /var/www

# Copy application files
COPY . .

# Copy compiled Vite assets from node-builder
COPY --from=node-builder /app/public/build ./public/build

# Install PHP dependencies optimized for production
RUN composer install --no-dev --optimize-autoloader --no-interaction --prefer-dist

# Set permissions
RUN chown -R www-data:www-data /var/www/storage /var/www/bootstrap/cache \
    && chmod -R 775 /var/www/storage /var/www/bootstrap/cache

# Copy Nginx, PHP-FPM pool tuning, Supervisor, and entrypoint
COPY docker/nginx.conf /etc/nginx/http.d/default.conf
COPY docker/zz-docker.conf /usr/local/etc/php-fpm.d/zz-docker.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
