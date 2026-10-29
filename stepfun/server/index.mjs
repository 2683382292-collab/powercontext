import { createServer } from "node:http";
import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from "seekdb";

const PORT = Number(process.env.STEPFUN_API_PORT || 43124);
const COLLECTION = process.env.STEPFUN_COLLECTION || "pocket_notes";
const DIMENSION = 512;

const client = new SeekdbClient({
  host: process.env.SEEKDB_HOST || "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASSWORD || "",
  database: process.env.SEEKDB_DATABASE || "oceanbase",
});

const schema = new Schema({
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: DIMENSION, distance: "cosine", type: "hnsw", lib: "vsag" },
    embeddingFunction: null,
  }),
});

let collectionPromise;

function collection() {
  collectionPromise ??= client.getOrCreateCollection({
    name: COLLECTION,
    schema,
    embeddingFunction: null,
  });
  return collectionPromise;
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function send(response, status, body) {
  const encoded = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": encoded.length,
    "Access-Control-Allow-Origin": "*",
  });
  response.end(encoded);
}

const server = createServer(async (request, response) => {
  if (request.method === "OPTIONS") {
    response.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    });
    response.end();
    return;
  }
  try {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/api/health") {
      const current = await collection();
      send(response, 200, { ok: true, collection: current.name, count: await current.count() });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/reset") {
      await client.deleteCollection(COLLECTION).catch(() => {});
      collectionPromise = undefined;
      await collection();
      send(response, 200, { ok: true, count: 0 });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/documents") {
      const body = await readBody(request);
      const docs = Array.isArray(body.documents) ? body.documents : [];
      if (!docs.length) {
        send(response, 400, { error: "documents is empty" });
        return;
      }
      const current = await collection();
      const started = Date.now();
      await current.add({
        ids: docs.map((doc) => String(doc.id)),
        documents: docs.map((doc) => String(doc.text)),
        embeddings: docs.map((doc) => doc.embedding),
        metadatas: docs.map((doc) => ({ title: String(doc.title || "笔记") })),
      });
      if (typeof current.refresh_index === "function") {
        await current.refresh_index();
      }
      send(response, 200, {
        ok: true,
        wrote: docs.length,
        count: await current.count(),
        writeMs: Date.now() - started,
      });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/search") {
      const body = await readBody(request);
      const query = String(body.query || "").trim();
      const embedding = body.embedding;
      if (!query || !Array.isArray(embedding)) {
        send(response, 400, { error: "query and embedding are required" });
        return;
      }
      const current = await collection();
      const started = Date.now();
      const found = await current.hybridSearch({
        query: { whereDocument: { $contains: query }, nResults: 20 },
        knn: { queryEmbeddings: [embedding], nResults: 20 },
        rank: { rrf: {} },
        nResults: 8,
        include: ["documents", "metadatas", "distances"],
      });
      const ids = found.ids?.[0] || [];
      const documents = found.documents?.[0] || [];
      const metadatas = found.metadatas?.[0] || [];
      const distances = found.distances?.[0] || [];
      send(response, 200, {
        ok: true,
        searchMs: Date.now() - started,
        engine: "seekdb.hybridSearch",
        results: ids.map((id, index) => ({
          id,
          text: documents[index] || "",
          title: metadatas[index]?.title || "笔记",
          distance: distances[index] ?? null,
        })),
      });
      return;
    }
    send(response, 404, { error: "not found" });
  } catch (error) {
    send(response, 500, { error: error instanceof Error ? error.message : String(error) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`pocket notes api http://127.0.0.1:${PORT}`);
});
