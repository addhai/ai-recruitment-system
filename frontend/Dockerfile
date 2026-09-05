# 多阶段构建：先 build 前端，再用 nginx 托管静态产物
FROM node:20-alpine AS builder

WORKDIR /app
# 国内网络：npm 走 npmmirror 镜像，清空 Docker 注入的宿主机死代理
RUN npm config set registry https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# SPA 路由 + /api、/sse 反代到后端
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
