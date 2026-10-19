# SHOP-TASK-096 积分到期的定时任务

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 3 条（积分批次在取得后 12 个月到期）及「失败、并发与重试」第 6 条（可重复运行、只生效一次，失败有告警与人工补跑办法），以及 HANDOFF 0.46 的 Kelvin 决定，把 SHOP-TASK-086 的 `expire_points` 接入 `app/jobs.py`。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.46。拆分：定时任务（3 个允许路径）。
- 任务与命令：`default_jobs()` 在既有四项后加入非 `own_transactions` 的 `expire_points`，运行器提交服务的 flush；每轮最多 `POINTS_EXPIRE_BATCH_LIMIT` 个批次，值与 `CANCEL_BATCH_LIMIT` 相同，余下留给下一轮。运营者可用 `python -m app.jobs expire-points` 手动补跑一次；失败仅记录任务名与异常类名，运行器照既有规则在连续失败满 `UNHEALTHY_AFTER_SECONDS` 后停止写心跳。
- 偏离与取舍：不改积分到期函数、模型、迁移、部署配置或设计；到期时刻边界仍由 `tests/test_points.py` 覆盖。运营告警邮件留给之后的运营告警邮件任务接入。
- 测试：`tests/test_jobs.py` 新增默认任务结构、到期一次性、分批续跑、失败回滚和手动命令的覆盖；每项说明对应 DESIGN 1.11 的句子。既有测试仅为第五项任务作机械修改：`tests/test_jobs.py` 的固定上限与数据库失败断言、`tests/test_auto_complete.py` 的注册顺序断言，以及 `tests/test_sms_retention.py` 的注册顺序断言。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/jobs.py 的 default_jobs() 在现有任务之后加入积分到期任务，名称为 expire_points（与现有任务名一样用下划线），每轮调用 SHOP-TASK-086 的到期函数，每批上限为新常量 POINTS_EXPIRE_BATCH_LIMIT（取 CANCEL_BATCH_LIMIT 的值），剩余的下一轮继续；不自己管理事务（由运行器的事务提交，到期函数只 flush）；日志只记任务名、处理的批次数与结果，不记会员、订单或积分数
      - 新增手动命令 python -m app.jobs expire-points——执行一次积分到期后退出（运营者补跑用，每次最多 POINTS_EXPIRE_BATCH_LIMIT 个批次）；模块说明的用法与任务列表随之补充；与其他任务相同，失败时只记日志、不退出，连续失败满 UNHEALTHY_AFTER_SECONDS 时停写心跳
      - tests/test_jobs.py 新增测试验证意图，照下面已实跑通过的写法——数据由辅助函数为每个批次建一名 active 会员，用该文件的 _order 建订单后把 member_id 设为该会员、claim_status 设为 CLAIM_NOT_CLAIMABLE（会员订单不得处于开放认领，否则违反 ck_orders_member_claim_not_open），flush 后用 earn_points 取得批次并提交；到期批次取得于 START 前 400 天，未到期批次取得于 START；每次读取 expire 流水后 rollback 测试会话，多轮之间先 clock.advance(60)。新增五条测试：其一，default_jobs() 的名称列表恰为现有四项加 expire_points，第五项 own_transactions 为假，POINTS_EXPIRE_BATCH_LIMIT 等于 CANCEL_BATCH_LIMIT（另起新测试，不加进既有测试）；其二，一轮后到期批次写一条 expire、未到期的不写，再跑一轮不变；其三，上限加一个到期批次，第一轮处理上限个、第二轮处理 1 个，并断言两轮的完成日志；其四，到期函数执行后抛异常时本轮 expire 流水回滚，日志只有任务名与异常类名；其五，main 执行 expire-points 返回 EXIT_OK 并写一条 expire。每条测试的说明写明它守住的是 DESIGN 1.11 的哪一句（其一的结构测试引用「失败、并发与重试」第 6 条），不写「SHOP-TASK-096 验收」；到期时刻的边界（Kelvin 0.46）由 tests/test_points.py 守住，本任务不重复，测试说明也不引用 0.46 的到期时刻；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - 既有测试只改下面四条、只为加入 expire_points——tests/test_jobs.py 的 test_default_jobs_use_the_fixed_round_limit 把名称列表断言改为前四项不变、长度为 5、第五项名称为 expire_points（不加其他断言，说明不改）；同文件 test_database_unavailable_is_logged_by_class_name_only 的会话次数 8 改为 10、每轮日志列表末尾加 expire_points 的失败行，注释随之改写；tests/test_auto_complete.py 的 test_job_is_registered_after_existing_jobs_with_own_transactions 与 tests/test_sms_retention.py 的 test_job_is_registered_last_with_own_transactions 改为前四项不变、第四项 own_transactions 为真、长度为 5、第五项名称为 expire_points；不改其他测试
      - docs/TODO.md 新增 SHOP-TASK-096 记录段（任务、命令、每批上限、偏离、验证到什么程度（按 CI 实际结果写），记下上一条四处既有测试的修改，并写明失败告警随运营告警邮件任务接入），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；现有任务的行为、顺序与命令不变；不改 app/services/points.py、部署配置与 docker-compose 文件、模型、迁移、.platform/、CI；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```
