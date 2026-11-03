// ai-hub merge verification entrypoint: runs the repo's full Python test
// suite with zero npm dependencies. Uses child_process.spawnSync without a
// shell, inherits stdio, and propagates the child exit code.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const TESTS = [
  "tests/template_sync.py",
  "tests/task_snapshot_tiers.py",
  "tests/smoke.py",
  "tests/fact_layer.py",
  "tests/hub_auto_lifecycle.py",
  "tests/concurrent_writes.py",
  "tests/vault_lock.py",
  "tests/maintenance_regressions.py",
  "tests/module_parity.py",
  "tests/protocol_smoke.py",
  "tests/http_smoke.py",
  "tests/repository_boundary.py",
  "tests/compact_note_truncation.py",
];

function pickPython() {
  for (const exe of ["python", "py"]) {
    const probed = spawnSync(exe, ["--version"], {
      stdio: "ignore",
      shell: false,
    });
    if (!probed.error && probed.status === 0) {
      return exe;
    }
  }
  return null;
}

const python = pickPython();
if (!python) {
  console.error(
    "ai-hub-verify: no Python interpreter found (tried 'python', 'py').",
  );
  process.exit(127);
}

for (const test of TESTS) {
  const run = spawnSync(python, [test], {
    cwd: ROOT,
    stdio: "inherit",
    shell: false,
  });
  if (run.error) {
    console.error(
      `ai-hub-verify: failed to launch ${python} ${test}: ${run.error.message}`,
    );
    process.exit(127);
  }
  if (run.status !== 0) {
    console.error(`ai-hub-verify: FAILED ${test} (exit ${run.status})`);
    process.exit(run.status ?? 1);
  }
}

console.log("ai-hub-verify: all green");
