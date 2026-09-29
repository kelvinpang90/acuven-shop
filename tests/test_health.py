"""健康检查是部署观察契约的依据。

deploy.yml 在上线后反复请求这个端点，直到响应里出现刚部署的 commit SHA 才判部署成功（D2、D3）。
所以这里钉住两件事：注入的 SHA 原样出现在响应里；没注入时不能碰巧冒充任何提交。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

DEPLOYED_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_healthz_reports_the_commit_the_image_was_built_from() -> None:
    client = TestClient(create_app(Settings(_env_file=None, git_sha=DEPLOYED_SHA)))

    response = client.get("/api/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "commit": DEPLOYED_SHA}


def test_an_image_built_without_a_sha_cannot_pass_the_deploy_check(monkeypatch) -> None:
    monkeypatch.delenv("SHOP_GIT_SHA", raising=False)
    client = TestClient(create_app(Settings(_env_file=None)))

    body = client.get("/api/healthz").text

    assert '"commit":"unknown"' in body
    assert DEPLOYED_SHA not in body
