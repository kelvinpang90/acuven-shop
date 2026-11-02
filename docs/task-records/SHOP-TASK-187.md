# SHOP-TASK-187 商品图片的 R2 存储读写（配置、共用 client、写入读取与删除）

- [x] 依据：`docs/DESIGN.md` 1.19（提交 `6e8f655`）「计价、优惠、积分与库存」第 13 条开头一段（R2 私有存储桶、四项配置、共用 client、超时与尝试次数、R2 调用失败的日志约束、存储不可用）；`docs/HANDOFF.md` 0.79 的落实写法（四项配置以 `validation_alias` 读取不带 `SHOP_` 前缀的原名；写入与读取共用一个 client、删除共用另一个 client；函数都是同步函数，由调用方放进线程）。设计闸门：DESIGN 1.19（提交 `6e8f655`）第 13 条与 HANDOFF 0.79。本任务只做存储读写，供 SHOP-TASK-189 至 191 的上传、提供与移除调用；不做图片校验与处理、数据库与接口，不改设计。
- [x] 配置（`app/core/config.py`）：`Settings` 新增 `r2_endpoint_url`、`r2_bucket`、`r2_access_key_id`、`r2_secret_access_key`，默认空串，分别以 `validation_alias` 读取 `R2_ENDPOINT_URL`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`（带 `SHOP_` 前缀的同名变量不生效）；`model_config` 与其他字段不变。
- [x] 新增 `app/services/image_store.py`：
  - `image_store_configured(settings: Settings) -> bool`：四项都不为空串时为 True，不另检查格式。
  - `put_image(settings: Settings, file_name: str, data: bytes) -> None`：写入键为 `product-images/<file_name>.webp`、`ContentType` 为 `image/webp` 的对象。
  - `get_image(settings: Settings, file_name: str) -> bytes`：返回该对象的字节；错误码为 `NoSuchKey` 或 HTTP 状态为 404 时抛 `ImageNotFound`、不记日志。
  - `delete_image(settings: Settings, file_name: str) -> None`：删除该对象；未配置时记一条 info 日志（操作名 delete 与文件名）后返回。
  - 三者都不检查 `file_name` 的格式；未配置时都不创建 client、不访问 R2，写入与读取抛 `ImageStoreUnavailable`。
  - 失败：写入与读取的 R2 调用抛出任何异常时记一条 error 日志并抛 `ImageStoreUnavailable`；删除出错只记一条 error 日志、不抛出。日志只含操作名（put、get、delete）、文件名与异常类名，不带 `exc_info`。两个异常类不带异常文本，一律 `raise … from None`。
- [x] client：`boto3.client("s3", endpoint_url=…, aws_access_key_id=…, aws_secret_access_key=…, region_name="auto", config=Config(connect_timeout=5, read_timeout=10, retries={"mode": "standard", "total_max_attempts": N}, request_checksum_calculation="when_required"))`。不调用 `boto3.set_stream_logger`、不改 botocore 的日志级别。
- [x] `tests/test_compose_env.py` 只改 `test_every_setting_is_passed_to_api`：跳过以原名读取的四个 `r2_` 字段（由同文件的 `test_compose_passes_r2_variables_only_to_api` 守住），其余断言与其他测试不变。
- [x] 测试：新增 `tests/test_image_store.py`，用 `botocore.stub.Stubber` 挂在本模块创建的 client 上，端点为不可解析的 `https://r2-endpoint.example.invalid`；每条测试的文档字符串写明它守住的第 13 条原句或 HANDOFF 0.79 的写法。覆盖四项配置以原名读取、带前缀的同名变量不生效、以原名作关键字参数构造；缺任一项或为空串即未配置，未配置时三个函数都不创建 client（写读抛 `ImageStoreUnavailable`，删除记 info 日志）；两个 client 的地区、端点、密钥、两项超时、尝试次数（写读 3、删除 1）与 `request_checksum_calculation`；连续两次调用得到同一个 client，写读删各两次只创建两个 client；不开 botocore 的调试日志；写入的 Bucket、Key、Body 与 ContentType；读取返回字节；删除的 Bucket 与 Key；`NoSuchKey` 与 404 为 `ImageNotFound` 且不记日志；其他 ClientError、读取超时与连不上为 `ImageStoreUnavailable`；删除出错不抛出；caplog 断言日志里有操作名、文件名与异常类名，没有 Stubber 给出的错误消息、端点、存储桶名与密钥；两个异常类不带文本、不带 `__cause__`。
- 两个 client 的取舍（HANDOFF 0.79）：设计写「进程内共用一个 boto3 client」，又要求写读至多 3 次尝试、删除只尝试 1 次；botocore 的尝试次数按 client 设定、不能逐次调用设定，所以写读共用一个 client（`read_write_client`，模块级变量 `_read_write_client`），删除共用另一个（`delete_client`，`_delete_client`），两者其余配置相同；都在首次使用时于同一把 `threading.Lock` 内创建，之后不再新建（之后的 `settings` 不再影响已建的 client）。测试可把这两个变量重置为 None。
- 偏离与取舍：
  - 取 client 也放在三个函数的 try 里：端点写错时 `boto3.client` 创建即抛出（异常文本带端点），这时同样按 R2 调用失败处理（写读抛 `ImageStoreUnavailable`、删除只记日志），不让原异常带着端点外泄。
  - 读取时 `Body.read()` 也在 try 里，读到一半超时同样归为 `ImageStoreUnavailable`。
  - 日志里的异常类名取 `type(exc).__name__`：botocore 会把已建模的 S3 错误码（如 `AccessDenied`）抛成 `ClientError` 的同名子类，所以测试不写死 `ClientError`，而以 `client.exceptions.from_code(code).__name__` 推出预期类名。
- 验证范围：上面列出的是本次新增的测试代码，全部用 Stubber 或替换 client 方法，不访问网络；真实 R2 的连通由运营者部署后核验。
