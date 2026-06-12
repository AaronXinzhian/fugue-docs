# services/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
业务逻辑层:被 app.py 调用,通过 storage 层读写数据。不直接操作文件。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| user_service.py | 用户增删查业务逻辑 | add_user(), list_users(), remove_user() |
| report.py | 生成团队名册文本报表 | build_report() |
| legacy_format.py | 旧版格式化工具(待退役) | format_date() |
