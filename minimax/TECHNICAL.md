# 技术说明

## 模块结构

`sddctx` 是调用入口，输入是命令名，输出是 JSON 上下文、代码差异或进程退出码。

- `materials.py`：在 seekdb 建表 `materials`，保存规格、代码和测试全文，并用 `MATCH ... AGAINST` 检索。
- `context.py`：用任务文件里的标识组成查询，只采纳检索命中的真实文件，组装规格、代码、测试和进度。
- `minimax_client.py`：把上下文发给 MiniMax，取回完整的 `ledger.py`。
- `state.py`：用固定的 Scope 幂等键把进度写入 PowerContext Memory，重启后按全文检索读回。
- `checks.py`：运行同一组 unittest，并记录命令、退出码和代码 sha256。

## 上下文怎么选出

导入时按 `sample_app/manifest.json` 读取磁盘文件。清单只决定资料库内容，不决定某次任务选中哪些文档。

`context` 从 `tasks/add-min-amount.md` 抽出规格标识、`filter_expenses`、`test_min_amount` 和 `test_category`，交给 seekdb 做全文检索。返回行必须带有这些标识，并且路径在仓库里真实存在。规格改写后再次 `import`，同一查询读到的是新正文。例如把闭区间句子换成带 `UPDATED-MIN-AMOUNT-EXCLUSIVE` 的句子，上下文摘录会跟着变化。

已有 PowerContext 状态时，上下文里的已完成事项、待办和验证结果来自那条状态。目标测试和回归测试都已通过时，待办被清空。

## 三项技术的分工

MiniMax 只负责生成修改后的 `filter_expenses`。请求体包含本次任务、检索上下文和修改前源码。仓库保存模型回复和统一差异，不保存密钥，也不保存思维链。

seekdb 保存工程资料索引。`EXPLAIN` 出现 `TEXT RETRIEVAL SCAN`，查询结果直接进入任务上下文。

PowerContext 使用 SQLite 档案保存任务状态。`verify` 写入验证结果后启动新的 Python 进程执行 `resume`，新进程重新打开同一个 Scope，检索状态并用于后续上下文。
