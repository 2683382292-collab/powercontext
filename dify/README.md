# 老汤的记仇酒馆

一位会记仇也会记恩的酒馆老板。旅客说一句话，老汤当面回嘴，并把这件事写进 PowerContext。换一场新对话、还是同一位旅客时，他会先翻旧账，再决定给不给汤。

## 怎么玩

1. 打开 Dify 里的「老汤的记仇酒馆」。
2. 先说一件普通的事，例如「老板，给我来一碗热汤」。
3. 再说一件离谱的事，例如「我把一只青蛙放进了你的汤里」。
4. **新开一场对话**（不要接着上一场的窗口记忆），再说「再来一碗，这次我付钱」。
5. 老汤应当提起青蛙，而不是把你当成第一次进门的人。

Dify 自己的对话窗口可以关掉。跨会话记得住，靠的是 PowerContext，不是聊天窗口。

## 分工

| 部分 | 做什么 |
| --- | --- |
| Dify Chatflow | 接待旅客、检索旧账、调用模型回嘴、把新账写回去 |
| PowerContext | 按旅客的 `scope_id` 保存和检索「旧账」 |
| seekdb | 本作品不使用 |

DSL 只有一个文件：`dsl/grumpy-innkeeper.yml`。导入这一个即可。

## 环境

- Dify 1.x，能导入 `version: 0.3.0` 的 advanced-chat DSL。本仓库按这个导出形状编写。
- 任意已在 Dify 里配好的对话模型。DSL 里先写着 `langgenius/openai/openai` 的 `gpt-4o-mini`，导入后如果提示模型不可用，在「老汤回嘴」节点改成你自己的模型。
- PowerContext Server，本地默认 SQLite 即可，不需要向量模型。接口以 PowerContext 的 `POST /v1/memory/search` 和 `POST /v1/memory/remember` 为准。
- 不需要插件。不要把真实 API Key 写进 DSL 或这份说明。

## 启动

1. 启动 PowerContext，并确认健康检查通过：

```bash
powercontext server run
curl --fail --silent --show-error http://127.0.0.1:8000/health/live
```

2. 为这位旅客建一个 scope，记下返回的 `scope_id`：

```bash
curl --fail --silent --show-error \
  --header 'Content-Type: application/json' \
  --data '{"title":"老汤的记仇酒馆","summary":"一位旅客的旧账","idempotency_key":"dify-grumpy-innkeeper"}' \
  http://127.0.0.1:8000/v1/scopes
```

要重来一轮，换一个新的 `idempotency_key`，或换一个新 scope。不要复用已经写过青蛙的 scope，否则第一句就会提青蛙。

3. 在 Dify 导入 `dsl/grumpy-innkeeper.yml`。
4. 打开应用的环境变量：
   - `POWERCONTEXT_URL`：PowerContext 根地址，不要带末尾斜杠。Dify 跑在 Docker 里时，`127.0.0.1` 指的是容器自己，要改成宿主机能被容器访问的地址，例如 `http://host.docker.internal:8000`。
   - `POWERCONTEXT_SCOPE`：上一步的 `scope_id`。
5. 确认「老汤回嘴」节点选中了一个可用对话模型。
6. 发布应用，从 Dify 的对话页进入。

Server 没开鉴权时，工作流不发送 Token。如果开了鉴权，在两个 HTTP 节点的 Header 里加上 `Authorization:Bearer <token>`，Token 放在 Dify 的密钥配置里，不要提交到仓库。

## 流程

`旅客开口` → `组装检索` → `翻旧账` → `老汤回嘴` → `拆回复并记账` → `记下这一笔` → `端给旅客`

检索词固定是「旧账」。每一笔记忆都会被写成以「旧账」开头的一句话，所以下一场对话即使没再提到青蛙，也能把旧账翻出来。模型只许使用检索结果，不许编造账上没有的事。

## 限制

- 一个 `POWERCONTEXT_SCOPE` 就是一位旅客。换人要换 scope。
- 单条记忆不超过 PowerContext 的文本上限。工作流会把新账压到大约 180 字。
- 没有对话模型时，Dify 应用发不出老汤的嘴。记忆本身可以用下面的脚本单独核对。
- 作品不调用 seekdb。

## 核对记忆

不启动模型时，也可以核对同一套记账是否跨轮还在：

```bash
python dify/scripts/verify_memory.py --output dify/evidence/memory-roundtrip.json
```

设置了 `POWERCONTEXT_URL` 时走 HTTP，和 DSL 里的两个节点相同。没设置时走本机 PowerContext runtime。成功时打印 `"ok": true`，第一轮能检索到青蛙，第二轮同时还有青蛙和付钱。

已记录的一次成功结果在 `evidence/memory-roundtrip.json`。样例步骤见 `samples/verification.md`。
