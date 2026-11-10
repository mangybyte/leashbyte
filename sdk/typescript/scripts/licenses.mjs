import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = execFileSync(
  "license-checker",
  ["--json", "--relativeLicensePath"],
  { cwd: root, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
);
const inventory = JSON.parse(output);
const approved = new Set([
  "(MIT AND CC-BY-3.0)",
  "(MIT OR CC0-1.0)",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "CC-BY-3.0",
  "CC0-1.0",
  "ISC",
  "MIT",
  "Python-2.0",
]);
const sanitized = {};
for (const [name, details] of Object.entries(inventory)) {
  if (name.startsWith("leashbyte@")) continue;
  const licenses = String(details.licenses ?? "");
  if (!approved.has(licenses)) {
    throw new Error(`Unapproved licence for ${name}: ${licenses || "missing"}`);
  }
  sanitized[name] = {
    licenses,
    ...(details.repository ? { repository: details.repository } : {}),
    ...(details.licenseFile ? { licenseFile: details.licenseFile } : {}),
  };
}
writeFileSync(
  resolve(root, "THIRD_PARTY_LICENSES.json"),
  `${JSON.stringify(sanitized, null, 2)}\n`,
);
console.log(`Checked ${Object.keys(sanitized).length} JavaScript packages.`);
