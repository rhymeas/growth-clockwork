import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { statSync } from "node:fs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const dashboardDirectory = path.resolve(scriptDirectory, "..");
const workspaceDirectory = path.resolve(dashboardDirectory, "..");
const viteBin = path.join(dashboardDirectory, "node_modules", "vite", "bin", "vite.js");

export function backendArguments(brokerDatabase, brokerPermissions) {
  const args = ["-m", "pipeline.review_api", "--workspace", workspaceDirectory, "--port", "8765"];
  if (brokerDatabase) {
    if (!path.isAbsolute(brokerDatabase) || !statSync(brokerDatabase).isFile()) {
      throw new Error("GROWTH_BROKER_DATABASE must name an existing database by absolute path.");
    }
    args.push("--broker-database", brokerDatabase);
  }
  if (brokerPermissions) {
    if (!path.isAbsolute(brokerPermissions) || !statSync(brokerPermissions).isFile()) {
      throw new Error("GROWTH_BROKER_PERMISSIONS must name an existing file by absolute path.");
    }
    args.push("--broker-permissions", brokerPermissions);
  }
  return args;
}

export function startDesk(spawnChild = spawn, brokerDatabase = process.env.GROWTH_BROKER_DATABASE,
  brokerPermissions = process.env.GROWTH_BROKER_PERMISSIONS) {
  // Validate before starting either service. A bad path must not leave half a Desk.
  const args = backendArguments(brokerDatabase, brokerPermissions);
  const children = [
    spawnChild("python3", args, { cwd: workspaceDirectory, stdio: "inherit" }),
    spawnChild(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", "4173", "--strictPort"], {
      cwd: dashboardDirectory,
      stdio: "inherit",
    }),
  ];
  let closing = false;
  function stop(exitCode = 0) {
    if (closing) return;
    closing = true;
    for (const child of children) child.kill("SIGTERM");
    process.exitCode = exitCode;
  }
  for (const child of children) {
    child.once("error", () => stop(1));
    child.once("exit", (code) => {
      if (!closing) stop(code || 1);
    });
  }
  return stop;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const stop = startDesk();
    process.once("SIGINT", () => stop());
    process.once("SIGTERM", () => stop());
  } catch (error) {
    console.error(`desk: ${error.message}`);
    process.exitCode = 1;
  }
}
