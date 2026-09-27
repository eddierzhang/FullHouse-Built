# Frontend: build with Vite, serve the static files with nginx.

FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Relative URLs, with nginx proxying /api to the backend -- so one image
# works on any host, with no CORS setup and no rebuild per domain.
ARG VITE_API_BASE=same-origin
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build


FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
