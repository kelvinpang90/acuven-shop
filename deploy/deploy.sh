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

# 主机级部署锁：同一台主机上的各项目部署一个接一个跑（约定见 vps_infra README「部署串行化」）。
# 起因：另一个项目部署成功后的 image prune 恰好落在本项目拉镜像的几秒里，containerd 报
# content digest not found / failed to extract layer（2026-10-27、11-03、11-14 三次）。
# 锁覆盖整个脚本：prune 与回滚也在锁内。进程退出（含 die）时由内核释放。
DEPLOY_LOCK="${ACUVEN_DEPLOY_LOCK:-/run/lock/acuven-deploy.lock}"
DEPLOY_LOCK_WAIT="${ACUVEN_DEPLOY_LOCK_WAIT:-300}"
command -v flock >/dev/null || die "flock is not installed; nothing has been changed"
# 先试追加写；文件属于别的用户时（protected_regular）退回只读打开，flock 对只读描述符同样有效。
# ⚠️ 2>/dev/null 必须放在 { } 外面：写成 `exec 9>>f 2>/dev/null` 时，exec 会把整段脚本此后的
#    stderr 永久丢掉（2026-11-14 billing 会话实测发现）。
{ exec 9>>"$DEPLOY_LOCK"; } 2>/dev/null || { exec 9<"$DEPLOY_LOCK"; } 2>/dev/null \
    || die "cannot open the host deploy lock $DEPLOY_LOCK; nothing has been changed"
if ! flock -n 9; then
    log "another deploy holds the host lock ($(head -1 "$DEPLOY_LOCK" 2>/dev/null || echo unknown)); waiting up to ${DEPLOY_LOCK_WAIT}s"
    flock -w "$DEPLOY_LOCK_WAIT" 9 \
        || die "the host deploy lock was not free within ${DEPLOY_LOCK_WAIT}s; nothing has been changed (re-run this job)"
fi
# 尽力写下持锁方，给排队的人看；写不进去不影响部署。
printf '%s %s pid=%s since=%s\n' "acuven_shop" "$SHA" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>/dev/null >"$DEPLOY_LOCK" || true
log "holding the host deploy lock"

export SHOP_API_IMAGE="${SHOP_IMAGE_REPO}-api:${SHA}"
export SHOP_WEB_IMAGE="${SHOP_IMAGE_REPO}-web:${SHA}"

# 回滚目标必须在任何改动之前记下：出事时正在跑的那一版就是回滚目标。
PREVIOUS_API="$(docker compose ps --format '{{.Image}}' shop_api)"
PREVIOUS_WEB="$(docker compose ps --format '{{.Image}}' shop_web)"
log "currently running: ${PREVIOUS_API:-nothing} / ${PREVIOUS_WEB:-nothing}"
# shop_jobs 与 shop_api 同一镜像（SHOP_API_IMAGE），这里只用来判断部署前它的容器是否存在。
# 用 -a：已停止的 shop_jobs 容器也算存在，不能当成首次上线把它移除。
PREVIOUS_JOBS="$(docker compose ps -a --format '{{.Image}}' shop_jobs)"
log "existing jobs container: ${PREVIOUS_JOBS:-nothing}"

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
    log "shop_jobs did not exist before this deploy; stopping and removing it"
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
