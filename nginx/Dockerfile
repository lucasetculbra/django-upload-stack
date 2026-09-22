# Baking the configuration into an image keeps the stack self-contained and avoids
# single-file bind mounts, which are fragile on Windows/OneDrive hosts.
FROM nginx:1.30-alpine

COPY default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
