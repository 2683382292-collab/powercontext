import { pipeline, env } from "@huggingface/transformers";

env.allowLocalModels = false;

const MODEL = "Xenova/bge-small-zh-v1.5";
const notes = document.querySelector("#notes");
const batchInput = document.querySelector("#batch");
const backendBadge = document.querySelector("#backend");
const importButton = document.querySelector("#import");
const sampleButton = document.querySelector("#sample");
const resetButton = document.querySelector("#reset");
const searchButton = document.querySelector("#search");
const queryInput = document.querySelector("#query");
const importStatus = document.querySelector("#import-status");
const searchStatus = document.querySelector("#search-status");
const results = document.querySelector("#results");

const SAMPLE = [
  "多肉浇水要等土干透。夏天放在有散射光的窗台，别中午暴晒。",
  "红烧肉先炒糖色，再下五花肉。一勺冰���就够，酱油少放才不会咸。",
  "周末想去老电影院看短片，爆米花不要太甜。",
  "夜里想喝一碗热汤，葱花后放，不要煮太久。",
  "跑步从三公里开始，膝盖不舒服就改成走路。",
  "书架第二层是旅行笔记，京都那本写了雨天的巴士。",
].join("\n\n");

let extractor;
let device = "wasm";

async function chooseDevice() {
  if (!navigator.gpu) return "wasm";
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return adapter ? "webgpu" : "wasm";
  } catch {
    return "wasm";
  }
}

async function loadModel() {
  device = await chooseDevice();
  backendBadge.textContent = device === "webgpu" ? "推理后端 WebGPU · INT8" : "推理后端 WASM · INT8（这台设备没有可用的 WebGPU）";
  importStatus.textContent = "正在把 INT8 模型载入浏览器…";
  extractor = await pipeline("feature-extraction", MODEL, {
    dtype: "q8",
    device,
  });
  importStatus.textContent = "模型已在本地就绪。放入笔记后写入 seekdb。";
}

function splitNotes(raw) {
  return raw
    .split(/\n\s*\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function embedBatch(texts) {
  const output = await extractor(texts, { pooling: "cls", normalize: true });
  const width = output.dims[output.dims.length - 1];
  const values = output.tolist ? output.tolist() : chunk(Array.from(output.data), width);
  return values;
}

function chunk(list, size) {
  const rows = [];
  for (let index = 0; index < list.length; index += size) rows.push(list.slice(index, index + size));
  return rows;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

importButton.addEventListener("click", async () => {
  const documents = splitNotes(notes.value);
  if (!documents.length) {
    importStatus.textContent = "先写至少一条笔记。";
    return;
  }
  importButton.disabled = true;
  const batchSize = Math.max(1, Number(batchInput.value) || 4);
  const started = performance.now();
  try {
    let embedded = 0;
    for (let offset = 0; offset < documents.length; offset += batchSize) {
      const slice = documents.slice(offset, offset + batchSize);
      const batchStarted = performance.now();
      const vectors = await embedBatch(slice);
      const embedMs = Math.round(performance.now() - batchStarted);
      await post("/api/documents", {
        documents: slice.map((text, index) => ({
          id: `note-${Date.now()}-${offset + index}`,
          title: text.slice(0, 18),
          text,
          embedding: vectors[index],
        })),
      });
      embedded += slice.length;
      importStatus.textContent = `已在${device === "webgpu" ? "WebGPU" : "WASM"}完成 ${embedded}/${documents.length} 条，这一批向量化 ${embedMs} ms。`;
    }
    importStatus.textContent = `写入完成，共 ${documents.length} 条，本地向量化 ${Math.round(performance.now() - started)} ms。索引已刷新，可以检索。`;
  } catch (error) {
    importStatus.textContent = `写入失败：${error.message}`;
  } finally {
    importButton.disabled = false;
  }
});

searchButton.addEventListener("click", async () => {
  const query = queryInput.value.trim();
  if (!query) return;
  searchButton.disabled = true;
  results.replaceChildren();
  try {
    const embedStarted = performance.now();
    const [vector] = await embedBatch([query]);
    const embedMs = Math.round(performance.now() - embedStarted);
    const found = await post("/api/search", { query, embedding: vector });
    searchStatus.textContent = `查询向量 ${embedMs} ms（${device === "webgpu" ? "WebGPU" : "WASM"}），seekdb 混合检索 ${found.searchMs} ms，返回 ${found.results.length} 条。`;
    for (const item of found.results) {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${item.title}</strong>${item.text}<span>seekdb hybridSearch</span>`;
      results.append(li);
    }
    if (!found.results.length) {
      searchStatus.textContent += " 没有同时靠近语义和关键词的笔记。";
    }
  } catch (error) {
    searchStatus.textContent = `检索失败：${error.message}`;
  } finally {
    searchButton.disabled = false;
  }
});

sampleButton.addEventListener("click", () => {
  notes.value = SAMPLE;
});

resetButton.addEventListener("click", async () => {
  resetButton.disabled = true;
  try {
    await post("/api/reset", {});
    results.replaceChildren();
    importStatus.textContent = "知识库已清空。";
  } catch (error) {
    importStatus.textContent = `清空失败：${error.message}`;
  } finally {
    resetButton.disabled = false;
  }
});

loadModel().catch((error) => {
  backendBadge.textContent = "模型没有载入";
  importStatus.textContent = error.message;
});
