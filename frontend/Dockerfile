FROM node:20-alpine AS build

WORKDIR /app

# 先 COPY 锁文件利用缓存
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .

ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

# 运行阶段
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# 默认空的 include 占位文件（被 tls overlay 覆盖时启用 SSL）
COPY nginx/_https_redirect.default.conf /etc/nginx/conf.d/_https_redirect.conf
COPY nginx/_ssl_server.default.conf /etc/nginx/conf.d/_ssl_server.conf

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1
