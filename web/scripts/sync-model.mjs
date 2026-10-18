// Copies the confidence calibrator from the API artifacts so the UI can explain each score (src/lib/explain.ts).
// The Docker build context is web/ only: there the committed copy in src/model/ is used as is.
import { copyFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../api/artifacts/calibrator.json");
const dst = resolve(here, "../src/model/calibrator.json");
if (existsSync(src)) {
  copyFileSync(src, dst);
  console.log("calibrator.json synced from api/artifacts");
}
