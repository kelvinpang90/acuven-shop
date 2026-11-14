# SHOP-TASK-264 部署脚本加主机级部署锁，部署 job 超时改为 17 分钟

- [x] 设计闸门：不适用。部署脚本与 CI 配置，不碰钱、不碰个人数据、不改数据库结构。依据 Kelvin 2026-11-14 批准的同主机部署串行化方案 D（vps_infra 的方案文档第 5.1、5.3、6、8 节）。由运营者在人工会话里实现，不交给 Worker。
- [x] `deploy/deploy.sh`：在 `SHOP_IMAGE_REPO` 检查之后、导出镜像变量与记下正在跑的版本之前，插入方案第 5.1 节的锁片段，项目名写 `acuven_shop`。片段与方案原文逐字相同（用脚本从方案文档取出代码块后比对）。
  - 默认锁文件写在脚本里。这是「不写服务器绝对路径」规则的例外，Kelvin 2026-11-14 批准，只限这一个锁文件路径。
  - 默认等锁 300 秒。`ACUVEN_DEPLOY_LOCK`、`ACUVEN_DEPLOY_LOCK_WAIT` 可覆盖锁文件与等待秒数。
  - 拿不到锁时，在碰 docker 之前以非零退出，日志写 `nothing has been changed (re-run this job)`。
  - 锁之后的顺序与各条失败语义不变：拉镜像、迁移、换容器、等健康、prune、回滚都在锁内，进程退出时由内核释放。
- [x] `.github/workflows/deploy.yml`：deploy job 的 `timeout-minutes` 由 12 改为 17，文件头 D5 注释的合计由 24 改为 29 分钟；Deploy 步骤加 `command_timeout: 13m`（`appleboy/ssh-action@v1.0.3` 默认 10 分钟，最坏情况等锁 5 分钟加部署与回滚约 7 分钟会在中途切断 SSH；设为 13 分钟，比约 12 分钟的最坏情况留 1 分钟余量；job 的 17 分钟保证这一步不会被 job 超时切断；之后的健康检查（每次请求都超时时最长约 10 分钟）若被 job 超时截断，结论同样是失败（D3），走回滚的部署不会执行健康检查。实施中由本会话发现，Kelvin 2026-11-14 批准，记为方案第 8 节第 11 条）。不改触发器（`on:`）、build job 与其他步骤。
- [x] 验证：在 ubuntu 24.04 容器里，用真 `flock`（util-linux 2.39.3）和一个记录调用的假 `docker` 跑本提交的 `deploy/deploy.sh`。假 docker 让 `compose pull` 失败，脚本在拉镜像处以「nothing has been changed」结束。
  - 锁被另一个进程占住 6 秒，`ACUVEN_DEPLOY_LOCK_WAIT=30`：日志先写 `another deploy holds the host lock (<持锁方那一行>); waiting up to 30s`，6 秒后写 `holding the host deploy lock`，之后才出现第一次 docker 调用；锁文件改写成本项目的持锁行。
  - 锁被占 30 秒，`ACUVEN_DEPLOY_LOCK_WAIT=3`：3 秒后以退出码 1 结束，日志写 `the host deploy lock was not free within 3s; nothing has been changed (re-run this job)`；假 docker 一次都没被调用；持锁方写的锁文件内容没被覆盖。
  - 补充：锁空闲、用默认锁文件时直接拿到锁；拿锁之后 docker 写到 stderr 的内容仍能透出（锁片段里的 `{ exec …; } 2>/dev/null` 没有吞掉脚本此后的 stderr）。
- 偏离与落实细节：
  - 插入点在 `SHOP_IMAGE_REPO` 检查（原第 42 行）之后紧接着，也就是两行镜像变量导出之前。这仍在「参数检查之后、第一次碰 docker 之前」。
  - 方案在 2026-11-14 实施期间改过一处：billing 会话实测发现，`exec 9>>f 2>/dev/null` 会把脚本此后的 stderr 永久丢掉，方案因此改成 `{ exec …; } 2>/dev/null`。本任务照抄的是改过后的版本。
- 交付：以 Draft PR 交付；CI 全绿、Codex 审查可合并后由运营者合并；合并部署后手工收尾。
