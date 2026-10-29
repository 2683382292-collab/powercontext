# 验证样例：青蛙还在账上

## 初始状态

使用一个**空的** PowerContext scope。不要用已经写过「青蛙」的 scope。

把 Dify 环境变量 `POWERCONTEXT_SCOPE` 设成这个 scope。Dify 对话窗口的历史可以忽略：两轮要**新开对话**，证明记得住的是 PowerContext。

## 第一轮

1. 新开一场对话。
2. 输入：`我把一只青蛙放进了你的汤里`
3. 预期：
   - 老汤的回复提到青蛙或汤被搞砸，语气像在记仇。
   - 工作流里「记下这一笔」返回成功。
   - 该 scope 的记忆里出现以「旧账」开头、并包含「青蛙」的一句。

开放式回复没有唯一原文。只要在骂这只青蛙、而且没有声称你以前还干过别的事，就算通过。

## 第二轮

1. 再新开一场对话，仍使用同一个 `POWERCONTEXT_SCOPE`。
2. 输入：`再来一碗，这次我付钱`
3. 预期：
   - 回复必须提起上一轮的青蛙，不能把旅客当成第一次进门。
   - 「翻旧账」节点的响应 `hits` 里能看到含「青蛙」的旧账。
   - 这一轮结束后，再检索「旧账」时，青蛙和「付钱」两句都还在。

## 已记录的 DSL HTTP 执行

`evidence/dsl-http-run.log` 是按当前 DSL 跑出来的两场对话。第一场检索 `hits` 为空，记账内容是「旧账：我把一只青蛙放进了你的汤里」。第二场是新对话，`POST /v1/memory/search` 的响应里有这句青蛙，回答也写出了「青蛙的账还在」。

对应的完整报文，包括 HTTP 状态码和 `server_access_log`，在 `evidence/dsl-http-run.json`。路径字段是 `dsl-code-nodes-and-http`。代码节点直接取自 `dsl/grumpy-innkeeper.yml`，检索和记账是发到该文件里两个 HTTP 节点的地址。

重新生成：

```bash
python dify/scripts/run_dsl_http.py --output dify/evidence/dsl-http-run.json
```

成功时进程退出码为 0，第二场检索结果包含「青蛙」，第二场回答也包含「青蛙」。

## 恢复初始状态

新建 scope（换一个 `idempotency_key`），把 Dify 的 `POWERCONTEXT_SCOPE` 改成新的 `scope_id`。旧 scope 可以留着，不要删，方便对照旧账还在。
