# 灯下技术说明

模型用公开的 `Xenova/bge-small-zh-v1.5`（BAAI/bge-small-zh-v1.5 的 ONNX 转换）。浏览器通过 Transformers.js 3 做 feature-extraction，CLS 池化并归一化，维度 512。应用固定加载 INT8 权重 `dtype: "q8"`，对应文件 `onnx/model_quantized.onnx`。量化前对照的是同一仓库的 FP32 `onnx/model.onnx`。

| 文件 | 字节 | MiB |
| --- | ---: | ---: |
| FP32 `model.onnx` | 94,851,877 | 90.46 |
| INT8 `model_quantized.onnx` | 24,010,842 | 22.90 |

压缩比 = 94,851,877 / 24,010,842 = **3.95**。只比较 ONNX 图，分词器两边共用。

8 条中文笔记、8 个查询上，FP32 与 INT8 的 seekdb 混合检索 Recall@3 都是 **8/8**。这组小评测集上看不到质量下降。计时在 ONNX Runtime CPU（与浏览器下载的是同一对文件）：8 条一起向量化，FP32 32 ms、INT8 9 ms；单条查询向量中位数 FP32 2 ms、INT8 1 ms；seekdb `hybridSearch` 中位数都是 3 ms。数字来自 `node scripts/measure.mjs`。

浏览器端用同一套 INT8 模型。先 `navigator.gpu.requestAdapter()`，成功就用 WebGPU，否则 WASM，徽章写明当前后端。本机无 WebGPU，Chrome 无头实测走 WASM：6 条示例笔记本地向量化 1597 ms；查询「想喝一碗热汤」的向量 7 ms，seekdb 混合检索 30 ms，第一条是喝汤那条笔记。

seekdb 集合 `pocket_notes`：IK 全文索引 + 512 维 cosine HNSW。浏览器把已经算好的向量和原文交给 Node 服务，服务只调用 `collection.hybridSearch`（全文 `$contains` 与 `queryEmbeddings`，RRF），不在客户端合并两路结果，服务端也不再做 embedding。`add` 之后调用 `refresh_index()`，所以新笔记在这次请求结束后就能被搜到。再次导入是追加写入，清空才删除集合。

批大小默认 4，用来减少一次推理的往返。质量没有为了速度换掉 INT8 模型。
