"""结账参考数据的公开只读接口：只有 GET /api/checkout/regions。

不需登录、不读写 cookie、不访问数据库（不用会话依赖）。
供结账页 P05 的电话国家码、收货国家与马来西亚州属下拉；数据见 app/services/regions.py。
国家名称不由接口提供，前端按界面语言本地化。数据只随代码与 phonenumbers 版本变化，
所以响应按静态数据缓存（Cache-Control: public, max-age=86400）。
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.services.regions import DEFAULT_PHONE_REGION, my_states, phone_regions

router = APIRouter(prefix="/api/checkout", tags=["checkout"])

CACHE_CONTROL = "public, max-age=86400"


class RegionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    calling_code: int


class MyStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str


class RegionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regions: list[RegionOut]
    my_states: list[MyStateOut]
    default_phone_region: str


@router.get("/regions", response_model=RegionsOut)
def regions(response: Response) -> RegionsOut:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return RegionsOut(
        regions=[RegionOut(code=r.code, calling_code=r.calling_code) for r in phone_regions()],
        my_states=[MyStateOut(code=s.code, name=s.name) for s in my_states()],
        default_phone_region=DEFAULT_PHONE_REGION,
    )
