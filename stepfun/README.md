# 灯下

在浏览器里把笔记变成向量，再交给本机 seekdb 做语义加全文的混合检索。文档向量和查询向量都在用户设备上计算，不调用云端推理接口。

## 启动

需要 Node.js 22、Chrome 或 Edge，以及已经在 `127.0.0.1:2881` 运行的 seekdb。默认用户 `root`，密码为空，库名 `oceanbase`。

```bash
cd stepfun
npm install --legacy-peer-deps
npm run dev
```

打开 http://127.0.0.1:43123 。页面会显示当前推理后端。这台机器没有 WebGPU 时会写成 WASM，检索流程仍然可用。

连接参数可以用环境变量改：`SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD`、`SEEKDB_DATABASE`。

## 体验

1. 点「放进示例笔记」，再点「写入 seekdb」。进度文字会显示本地向量化耗时。
2. 搜索「想喝一碗热汤」。第一条应是夜里喝汤那条笔记。
3. 再贴一条新笔记并写入。写入完成后立刻能被搜到，不需要重启。
4. 「清空知识库」会删掉 seekdb 里的 `pocket_notes` 集合。

模型是 Hugging Face 上的 `Xenova/bge-small-zh-v1.5`，浏览器加载 INT8 ONNX（`onnx/model_quantized.onnx`）。首次打开会下载模型文件，之后由浏览器缓存。

实测和量化对比写在 `TECHNICAL.md`。
