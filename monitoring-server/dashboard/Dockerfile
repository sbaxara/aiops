# Stage 1 — Build the React/Vite app
FROM node:26-alpine AS builder

WORKDIR /app

# Copy package.json and install dependencies
COPY package.json ./
RUN npm install

# Copy all source files
COPY . .

# Release version shown in the UI: AXARA_VERSION when set (release builds), else package.json
ARG AXARA_VERSION=
ENV AXARA_VERSION=${AXARA_VERSION}

# Build the app (Vite outputs to /app/dist)
RUN npm run build

# Stage 2 — Serve with Nginx
FROM nginx:stable-alpine

# Copy Vite build output to nginx
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]