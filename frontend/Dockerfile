# syntax=docker/dockerfile:1
#
# 前端镜像：node 构建静态产物，nginx 发出去。运行期没有 node。
# node 大版本与 CI 的 setup-node 保持一致，两边漂移会出现「CI 绿而镜像构建失败」。

FROM node:24-slim AS build
WORKDIR /src
# 先只 COPY 锁文件装依赖，改一行组件不必重装全部包。npm ci 严格照锁文件装。
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src
COPY public ./public
# build 同时跑类型检查（tsc --build && vite build）。
RUN npm run build

FROM nginx:1.27-alpine AS runtime
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/shop-web.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
