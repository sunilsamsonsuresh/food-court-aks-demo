# Official nginx image, small Alpine Linux version.
FROM nginx:1.27-alpine

# Default backend addresses: the container names on our Docker network.
# Override them at run time with `docker run -e MENU_SERVICE_URL=...`
# (or in Kubernetes later) without rebuilding the image.
ENV MENU_SERVICE_URL=http://menu-service:8000
ENV ORDER_SERVICE_URL=http://order-service:8000

# The web page goes into nginx's default web folder.
COPY html/index.html /usr/share/nginx/html/index.html

# How the template becomes the real config:
# At container start, a script built into the nginx image looks at every
# *.template file in /etc/nginx/templates/, fills in ${VARIABLES} from the
# environment, and writes the result to /etc/nginx/conf.d/ without the
# ".template" ending. So this file becomes /etc/nginx/conf.d/default.conf,
# replacing nginx's built-in default site. Then nginx starts.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
# No CMD needed: the nginx image already starts nginx for us.
