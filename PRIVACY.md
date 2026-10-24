# Privacy Policy

本插件（`cost-aware-strategy`）不收集、不存储、不传输任何用户数据。

- **成本估算**只读取你在插件参数里显式提供的 `table_metadata`、`cost_overrides`、
  `database_tools` 等配置，以及当次工具调用的入参文本（SQL 语句或工具名）。
- **不访问数据库**：不执行 `EXPLAIN`、不查询系统表、不建立任何数据库连接。
- **不写入**：预算账本与成本报告只存在于单次 Agent 运行的内存中，运行结束即丢弃。
- **不外发**：除 Dify 宿主自身的模型/工具调用外，本插件不向任何第三方服务发送数据，
  也不包含遥测、埋点或远程日志。

如有疑问，请在 <https://github.com/mrkeyoe/dify-cost-aware-strategy/issues> 提交 issue。