#!/usr/bin/env bash
#
# 把一个提交的镜像部署到本机的生产栈。由 .github/workflows/deploy.yml 经 SSH 调用。
#
# 用法（在服务器上本仓库检出的根目录里）：
#     SHOP_IMAGE_REPO=ghcr.io/所有者/仓库名 deploy/deploy.sh 完整的40位commitSHA
#
# 顺序：记下正在跑的版本 → 拉新镜像 → 迁移 → 换容器 → 等健康 → 不健康则回滚。
# 成功才以 0 退出；迁移失败、换容器失败、不健康、回滚（无论回滚成败）都以非零退出（部署观察契约 D3）。
# 迁移只能是向后兼容的（加表、加列、加索引）：迁移跑完而新容器没起来时，旧版本要能继续对着新表结构工作。

set -euo pipefail

SHA="${1:-}"
HEALTH_TIMEOUT_SECONDS="${SHOP_HEALTH_TIMEOUT_SECONDS:-180}"

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# 参数里列出的服务（不给参数即 compose 文件里的全部服务）都在、且都报 healthy 才算健康。
wait_for_health() {
    local deadline expected healthy status
    deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    if [ "$#" -gt 0 ]; then
        expected="$#"
    else
        expected="$(docker compose config --services | wc -l)"
    fi
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if status="$(docker compose ps --format '{{.Service}} {{.Health}}' "$@")"; then
            healthy="$(printf '%s\n' "$status" | awk '$2 == "healthy"' | wc -l)"
            if [ "$healthy" -eq "$expected" ]; then
                return 0
            fi
        fi
        sleep 5
    done
    return 1
}

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "usage: deploy/deploy.sh <full 40-character lowercase commit SHA>"
[ -n "${SHOP_IMAGE_REPO:-}" ] || die "SHOP_IMAGE_REPO is not set"

export SHOP_API_IMAGE="${SHOP_IMAGE_REPO}-api:${SHA}"
export SHOP_WEB_IMAGE="${SHOP_IMAGE_REPO}-web:${SHA}"

# 回滚目标必须在任何改动之前记下：出事时正在跑的那一版就是回滚目标。
PREVIOUS_API="$(docker compose ps --format '{{.Image}}' shop_api)"
PREVIOUS_WEB="$(docker compose ps --format '{{.Image}}' shop_web)"
log "currently running: ${PREVIOUS_API:-nothing} / ${PREVIOUS_WEB:-nothing}"
# shop_jobs 与 shop_api 同一镜像（SHOP_API_IMAGE），这里只用来判断部署前它是否在跑。
PREVIOUS_JOBS="$(docker compose ps --format '{{.Image}}' shop_jobs)"
log "currently running jobs: ${PREVIOUS_JOBS:-nothing}"

log "pulling $SHOP_API_IMAGE and $SHOP_WEB_IMAGE"
docker compose pull --quiet || die "pull failed; nothing has been changed"

log "running migrations"
docker compose run --rm --no-deps shop_api alembic upgrade head \
    || die "migration failed; the previous version is still running"

log "starting $SHA"
HEALTHY=0
if docker compose up -d --no-build && wait_for_health; then
    HEALTHY=1
fi

if [ "$HEALTHY" = "1" ]; then
    log "deployed $SHA"
    # 每次部署都拉进一对新 SHA 标签的镜像，不清理会慢慢吃满共享服务器的磁盘。
    # 只清本项目的（构建时打了这个标签）、且没有任何容器在用的；正在跑的这一版不会被动到。
    # 清理失败不改变部署结论，只记一条警告。
    if ! docker image prune --all --force --filter "label=acuven.project=acuven_shop" >/dev/null; then
        log "WARNING: could not prune old images"
    fi
    exit 0
fi

log "deploy of $SHA is not healthy"
docker compose ps

if [ -z "$PREVIOUS_API" ] || [ -z "$PREVIOUS_WEB" ]; then
    # 首次部署没有回滚目标：保留现场给人排查，不把栈停掉。
    die "nothing to roll back to; leaving the stack up for inspection"
fi

log "rolling back to $PREVIOUS_API / $PREVIOUS_WEB"
export SHOP_API_IMAGE="$PREVIOUS_API"
export SHOP_WEB_IMAGE="$PREVIOUS_WEB"
ROLLED_BACK=0
if [ -n "$PREVIOUS_JOBS" ]; then
    docker compose up -d --no-build || die "rollback failed; manual intervention required"
    if wait_for_health; then
        ROLLED_BACK=1
    fi
else
    # 部署前没有 shop_jobs（这次是首次上线它）：旧版本没有这个服务，回滚只以旧镜像重启
    # shop_api 与 shop_web，停止并移除新起的 shop_jobs，健康只计这两个服务。
    log "shop_jobs was not running before this deploy; stopping and removing it"
    docker compose rm --stop --force shop_jobs || die "rollback failed; manual intervention required"
    docker compose up -d --no-build shop_api shop_web \
        || die "rollback failed; manual intervention required"
    if wait_for_health shop_api shop_web; then
        ROLLED_BACK=1
    fi
fi
if [ "$ROLLED_BACK" = "1" ]; then
    # 回滚成功也以非零退出：这个提交没能上线，部署结论必须是失败。
    die "rolled back to $PREVIOUS_API; the deploy of $SHA failed"
fi
die "rollback did not become healthy; manual intervention required"
