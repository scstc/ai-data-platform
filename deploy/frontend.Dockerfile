# 前端镜像:Umi Max(utoopack)构建静态产物 → nginx 托管 + 反代 /api 到后端。
# 构建上下文 = 仓库根目录(与后端共用,便于 deploy/nginx.conf 一并拷入)。
FROM node:22-slim AS build
WORKDIR /app
ENV HUSKY=0
# 先装依赖,再拷源码。用 npm install(非 ci):仓库 package-lock 与 package.json
# 存在漂移(@utoo/pack 等未写回 lock),ci 严格校验会拒装;install 按 package.json 解析。
COPY frontend/package.json frontend/package-lock.json ./
# 大依赖树在弱网/代理环境下偶发 ECONNRESET(连接中途被重置,npm 内置重试覆盖不到),外层重试兜底
RUN npm config set fetch-retries 5 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-retry-maxtimeout 120000 \
    && ( npm install --no-audit --no-fund --legacy-peer-deps \
         || (echo "npm install failed, retry 1/2..." && sleep 5 && npm install --no-audit --no-fund --legacy-peer-deps) \
         || (echo "npm install failed, retry 2/2..." && sleep 5 && npm install --no-audit --no-fund --legacy-peer-deps) )
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
