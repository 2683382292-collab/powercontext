import { pipeline } from "@huggingface/transformers";
import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from "seekdb";

const MODEL = "Xenova/bge-small-zh-v1.5";
const DOCS = [
  ["succulent", "多肉浇水要等土干透。夏天放在有散射光的窗台，别中午暴晒。"],
  ["pork", "红烧肉先炒糖色，再下五花肉。一勺冰糖就够，酱油少放才不会咸。"],
  ["film", "周末想去老电影院看短片，爆米花不要太甜。"],
  ["soup", "夜里想喝一碗热汤，葱花后放，不要煮太久。"],
  ["run", "跑步从三公里开始，膝盖不舒服就改成走路。"],
  ["kyoto", "书架第二层是旅行笔记，京都那本写了雨天的巴士。"],
  ["cat", "猫砂盆两天铲一次，否则客厅会有味道。"],
  ["filmroll", "相机里还有半卷没拍完的胶卷，别打开后盖。"],
];
const QUERIES = [
  ["怎么给多肉浇水", "succulent"],
  ["红烧肉放多少糖", "pork"],
  ["周末去看独立短片", "film"],
  ["想喝一碗热汤", "soup"],
  ["跑步膝盖疼怎么办", "run"],
  ["京都下雨坐什么", "kyoto"],
  ["猫砂怎么清理", "cat"],
  ["胶卷还能用吗", "filmroll"],
];

function schema() {
  return new Schema({
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: 512, distance: "cosine", type: "hnsw", lib: "vsag" },
      embeddingFunction: null,
    }),
  });
}

function rows(output) {
  const width = output.dims.at(-1);
  const data = Array.from(output.data);
  const vectors = [];
  for (let index = 0; index < output.dims[0]; index += 1) {
    vectors.push(data.slice(index * width, (index + 1) * width));
  }
  return vectors;
}

async function embedAll(dtype) {
  const extractor = await pipeline("feature-extraction", MODEL, { dtype, device: "cpu" });
  const texts = DOCS.map(([, text]) => text);
  const started = Date.now();
  const docVectors = rows(await extractor(texts, { pooling: "cls", normalize: true }));
  const batchMs = Date.now() - started;
  const queryVectors = [];
  const singles = [];
  for (const [text] of QUERIES) {
    const one = Date.now();
    queryVectors.push(rows(await extractor(text, { pooling: "cls", normalize: true }))[0]);
    singles.push(Date.now() - one);
  }
  singles.sort((a, b) => a - b);
  return { docVectors, queryVectors, batchMs, medianQueryMs: singles[Math.floor(singles.length / 2)] };
}

async function recall(client, name, docVectors, queryVectors) {
  await client.deleteCollection(name).catch(() => {});
  const collection = await client.getOrCreateCollection({ name, schema: schema(), embeddingFunction: null });
  const writeStarted = Date.now();
  await collection.add({
    ids: DOCS.map(([id]) => id),
    documents: DOCS.map(([, text]) => text),
    embeddings: docVectors,
    metadatas: DOCS.map(([id]) => ({ title: id })),
  });
  if (collection.refresh_index) await collection.refresh_index();
  const writeMs = Date.now() - writeStarted;
  let hits = 0;
  const searches = [];
  for (const [query, expected] of QUERIES) {
    const vector = queryVectors[QUERIES.findIndex(([text]) => text === query)];
    const started = Date.now();
    const found = await collection.hybridSearch({
      query: { whereDocument: { $contains: query }, nResults: 8 },
      knn: { queryEmbeddings: [vector], nResults: 8 },
      rank: { rrf: {} },
      nResults: 3,
      include: ["documents"],
    });
    searches.push(Date.now() - started);
    const ids = found.ids?.[0] || [];
    if (ids.includes(expected)) hits += 1;
  }
  searches.sort((a, b) => a - b);
  await client.deleteCollection(name);
  return {
    recallAt3: hits / QUERIES.length,
    hits,
    total: QUERIES.length,
    writeMs,
    medianSearchMs: searches[Math.floor(searches.length / 2)],
  };
}

const client = new SeekdbClient({
  host: "127.0.0.1",
  port: 2881,
  user: "root",
  password: "",
  database: "oceanbase",
});
const fp32 = await embedAll("fp32");
const q8 = await embedAll("q8");
const fp32Search = await recall(client, "pocket_measure_fp32", fp32.docVectors, fp32.queryVectors);
const q8Search = await recall(client, "pocket_measure_q8", q8.docVectors, q8.queryVectors);
await client.close();
console.log(JSON.stringify({
  model: MODEL,
  fp32: { batchMs: fp32.batchMs, medianQueryMs: fp32.medianQueryMs, ...fp32Search },
  q8: { batchMs: q8.batchMs, medianQueryMs: q8.medianQueryMs, ...q8Search },
}, null, 2));
