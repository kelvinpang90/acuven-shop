# syntax=docker/dockerfile:1
#
# 后端镜像。两阶段：build 造 wheel，runtime 只装 wheel，构建工具不留在运行镜像里。

FROM python:3.12-slim AS build
WORKDIR /src
# 只 COPY 打包需要的东西：改一个 Markdown 不该让 wheel 重建。
COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir build==1.2.2.post1 \
    && python -m build --wheel --outdir /dist

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# WORKDIR 不能叫 /app：cwd 会进 sys.path，同名目录会和装好的 app 包抢 `import app`。
WORKDIR /srv/shop
# alembic/ 不在 wheel 里（wheel 只打 app* 包），部署时的 `alembic upgrade head` 要它。
COPY alembic.ini ./
COPY alembic ./alembic

# 放在最后：每个提交的 SHA 都不同，放前面会让之后所有层的缓存失效。
# 健康检查把它原样返回，部署工作流据此确认线上跑的就是这个提交。
ARG GIT_SHA=unknown
ENV SHOP_GIT_SHA=${GIT_SHA}

USER 10001:10001
EXPOSE 8000
# 迁移不是启动副作用：由 deploy/deploy.sh 在换容器之前显式跑一次。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
