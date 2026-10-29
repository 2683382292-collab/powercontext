# 规有所依：SDD 上下文模块

这个目录完成一次可运行的局部修改：给 `sample_app/ledger.py` 的 `filter_expenses` 增加闭区间 `min_amount` 筛选，并保持原有 `category` 行为。

上下文模块的入口是 `python -m sddctx`。它把规格、代码和测试放进 seekdb，按任务检索出开发上下文，交给 MiniMax 改代码，再用 PowerContext 保存任务状态。模块重启后重新读取该状态，并据此去掉已经完成的待办。

## 版本

| 组件 | 版本与接入方式 |
| --- | --- |
| Python | 3.12 |
| seekdb | 嵌入式 `pylibseekdb==1.3.0.post4`，查询使用 PyMySQL `1.2.0` |
| PowerContext | `2026-competition` 的 `dab240063bfcfa7917898dc44da4baadb3747bd0`，Python API：`open_builtin_runtime`，SQLite `sqlite+aiosqlite` |
| MiniMax | Anthropic 兼容接口 `POST {MINIMAX_BASE_URL}/v1/messages`，请求头 `Authorization: Bearer`。默认基址 `https://api.minimaxi.com/anthropic`，默认模型 `MiniMax-M3` |

业务工程没有自己的数据库。工程资料只放在 seekdb，任务进展只放在 PowerContext。

## 准备

生态分支本身没有 PowerContext 库源码。先在旁边克隆库并安装，再进入作品的 `minimax/` 目录。下面的命令在 Linux 和 WSL 里执行过：Python 3.12、`pip install -e ".[builtin]"`、然后安装 `requirements.txt`。

```bash
git clone --branch 2026-competition https://github.com/oceanbase/powercontext.git
git -C powercontext checkout dab240063bfcfa7917898dc44da4baadb3747bd0
python -m pip install -e "./powercontext[builtin]"
cd minimax
python -m pip install -r requirements.txt
export MINIMAX_API_KEY=你的TokenPlan密钥
export MINIMAX_MODEL=MiniMax-M3
export MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic
```

在 Windows cmd 里，把 `export` 换成 `set`。国际站基址是 `https://api.minimax.io/anthropic`，另一组国内基址是 `https://api.minimax.cn/anthropic`。模型名以 Token Plan 控制台显示的为准，例如 `MiniMax-M3`、`MiniMax-M3.1-Flash` 或 `MiniMax-M2.5`。密钥只放在环境变量里，不要写入仓库。

`pylibseekdb 1.3.0.post4` 提供 Linux 轮子。Windows 上请在 WSL 里执行。安装完成后，在 `minimax/` 目录直接运行 `python -m sddctx`，不需要额外的 `PYTHONPATH`。

## 命令

```bat
python -m sddctx init
python -m sddctx import
python -m sddctx context
python -m sddctx develop
python -m sddctx verify
```

`context` 打印本次任务的规格、代码、测试、已完成事项、待办和已有验证结果。来源路径都是仓库里的真实文件。

`develop` 把这份上下文和当前 `ledger.py` 发给 MiniMax，写回模块，并保存：

- `records/minimax/development.json`：任务、上下文、模型回复
- `records/minimax/applied.diff`：与提交代码对应的差异

`verify` 从快照恢复修改前代码，然后依次检查：

1. 目标测试因断言失败而失败，类别回归测试通过。
2. 首次上下文含有「金额下界按闭区间比较。」
3. 改写规格并重新导入后，上下文改为「UPDATED-MIN-AMOUNT-EXCLUSIVE」，不再保留旧句子。
4. 使用已保存的 MiniMax 修改，或在没有记录时重新调用 MiniMax。
5. 同一组目标测试和回归测试全部通过。日志含命令、退出码和 `ledger.py` 的 sha256。
6. 把验证结果写入 PowerContext，再启动新进程执行 `resume`。新进程读到通过记录后，待办变为空。

单独查看恢复结果：

```bat
python -m sddctx resume
```

## 验证样例

任务文件：`tasks/add-min-amount.md`。

要改什么：实现 `SPEC-AMT-001`，`min_amount` 按闭区间过滤，并且可以和 `category` 同时生效。

必须保持：`SPEC-CAT-001` 与 `SPEC-COMPAT-001`。只按类别筛选、省略类别、空列表，这三项结果不变。

上下文必须包含：

| 类型 | 标识 | 文件 |
| --- | --- | --- |
| 规格 | SPEC-AMT-001 | `sample_app/specs/amount.md` |
| 规格 | SPEC-CAT-001 | `sample_app/specs/category.md` |
| 规格 | SPEC-COMPAT-001 | `sample_app/specs/compat.md` |
| 代码 | filter_expenses | `sample_app/ledger.py` |
| 测试 | test_min_amount | `sample_app/tests/test_min_amount.py` |
| 测试 | test_category | `sample_app/tests/test_category.py` |

初始数据在测试文件的 `EXPENSES`。修改前代码在 `sample_app/snapshots/before/ledger.py`，与提交时的 `sample_app/ledger.py` 相同。

直接运行测试：

```bat
cd sample_app
python -m unittest discover -s tests -v
python -m unittest tests.test_category -v
```

修改前，`discover` 的退出码是 1，`test_min_amount` 出现 `AssertionError`。`tests.test_category` 的退出码是 0。

## 重置

```bat
python -m sddctx init
copy /Y sample_app\snapshots\before\ledger.py sample_app\ledger.py
```

`init` 删除 `data/seekdb` 和 `data/task-state.sqlite`。规格文件如果停在更新句子，把 `sample_app/specs/amount.md` 中的半开区间句子改回「金额下界按闭区间比较。」`verify` 自己会做这两步。

## 已知限制

- 没有 `MINIMAX_API_KEY`，也没有 `records/minimax/development.json` 时，`develop` 和 `verify` 会停止，不会编造模型记录。
- 状态复用恢复的是任务进度和验证结果，不是完整的模型会话。
- seekdb 使用 `WITH PARSER SPACE`。任务检索词是规格标识、函数名和测试名。
