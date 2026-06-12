# storage/ — 模块索引(L2)

> 本文件夹内文件增删、重命名、接口变更时,必须更新本文件。上级索引:[../PROJECT_INDEX.md](../PROJECT_INDEX.md)

## 模块定位
持久化层:唯一允许读写数据文件的模块,向 services 层提供加载/保存接口。

## 文件清单
| 文件 | 职责 | 关键导出 |
|------|------|----------|
| store.py | 用户列表的 JSON 文件读写 | load_users(), save_users(), DATA_PATH |
