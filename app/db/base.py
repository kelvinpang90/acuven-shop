"""SQLAlchemy 声明式基类。

命名约定让主键、外键、唯一、检查约束与索引都有确定的名字，
迁移里写同样的名字，以后改表、删约束不必去库里查自动生成的名字。
生成的名字须在 MySQL 标识符的 64 字符以内，表名与列名据此取得较短。
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}

# 每张表都显式写：InnoDB 才有外键与事务；utf8mb4 存得下三语文案的全部字符。
MYSQL_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
