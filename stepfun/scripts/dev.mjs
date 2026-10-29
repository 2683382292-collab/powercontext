import { spawn } from "node:child_process";

const server = spawn("node", ["server/index.mjs"], { stdio: "inherit" });
const web = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", "43123"], { stdio: "inherit" });

function stop() {
  server.kill();
  web.kill();
  process.exit(0);
}

process.on("SIGINT", stop);
process.on("SIGTERM", stop);
