import crypto from "node:crypto";
import path from "node:path";
import { execFileSync } from "node:child_process";

const projectRoot = path.resolve(import.meta.dirname, "..");
const manifestPath = "artifacts/evidence/manifest.json";
const evidencePrefix = "artifacts/evidence/";
const printIndex = process.argv.includes("--print-source-index");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function git(args, options = {}) {
  return execFileSync(
    "git",
    ["-c", `safe.directory=${projectRoot.replaceAll("\\", "/")}`, ...args],
    { cwd: projectRoot, encoding: null, maxBuffer: 128 * 1024 * 1024, ...options },
  );
}

function fail(message) {
  throw new Error(`Evidence verification failed: ${message}`);
}

function trackedIndex() {
  const entries = new Map();
  const records = git(["ls-files", "--stage", "-z"]).toString("utf8").split("\0").filter(Boolean);
  for (const record of records) {
    const separator = record.indexOf("\t");
    if (separator < 0) fail(`could not parse Git index record ${JSON.stringify(record)}`);
    const [mode, objectId, stage] = record.slice(0, separator).split(" ");
    const file = record.slice(separator + 1).replaceAll("\\", "/");
    if (stage !== "0") fail(`${file} has unresolved index stage ${stage}`);
    entries.set(file, { mode, objectId });
  }
  return entries;
}

function blob(entry) {
  return git(["cat-file", "blob", entry.objectId]);
}

function indexSnapshot(entries) {
  const sourceFiles = [...entries.keys()]
    .filter((file) => !file.startsWith(evidencePrefix))
    .sort();
  const sourceDigest = crypto.createHash("sha256");
  for (const file of sourceFiles) {
    sourceDigest.update(`${file}\0${sha256(blob(entries.get(file)))}\n`, "utf8");
  }
  const artifacts = [...entries.keys()]
    .filter((file) => file.startsWith(evidencePrefix) && file !== manifestPath)
    .sort()
    .map((file) => {
      const body = blob(entries.get(file));
      return { path: file, bytes: body.length, sha256: sha256(body) };
    });
  return {
    sourceFiles: sourceFiles.length,
    sourceSha256: sourceDigest.digest("hex"),
    artifacts,
  };
}

const entries = trackedIndex();
if (!printIndex) {
  const status = git(["status", "--porcelain=v1", "--untracked-files=all", "-z"]);
  if (status.length !== 0) {
    const paths = status
      .toString("utf8")
      .split("\0")
      .filter(Boolean)
      .slice(0, 12)
      .join(", ");
    fail(`the published-tree proof requires a clean tracked tree; found ${paths}`);
  }
}
const snapshot = indexSnapshot(entries);

if (printIndex) {
  process.stdout.write(`${JSON.stringify(snapshot, null, 2)}\n`);
  process.exit(0);
}
if (!entries.has(manifestPath)) {
  fail(`${manifestPath} is not tracked`);
}

const manifest = JSON.parse(blob(entries.get(manifestPath)).toString("utf8"));
if (manifest.schema !== "feed-passport/evidence-manifest/v3") {
  fail(`unsupported manifest schema ${String(manifest.schema)}`);
}

const declaredPaths = manifest.artifacts.map((item) => item.path).sort();
const observedPaths = snapshot.artifacts.map((item) => item.path);
if (JSON.stringify(declaredPaths) !== JSON.stringify(observedPaths)) {
  fail(`tracked artifact inventory differs\ndeclared=${JSON.stringify(declaredPaths)}\nobserved=${JSON.stringify(observedPaths)}`);
}

const observedArtifacts = new Map(snapshot.artifacts.map((artifact) => [artifact.path, artifact]));
for (const artifact of manifest.artifacts) {
  const observed = observedArtifacts.get(artifact.path);
  if (observed.bytes !== artifact.bytes) {
    fail(`${artifact.path} has ${observed.bytes} indexed bytes; expected ${artifact.bytes}`);
  }
  if (observed.sha256 !== artifact.sha256) {
    fail(`${artifact.path} has indexed SHA-256 ${observed.sha256}; expected ${artifact.sha256}`);
  }
}

if (snapshot.sourceFiles !== manifest.source.snapshot_file_count) {
  fail(`source snapshot has ${snapshot.sourceFiles} indexed files; expected ${manifest.source.snapshot_file_count}`);
}
if (snapshot.sourceSha256 !== manifest.source.snapshot_sha256) {
  fail(`source snapshot has indexed SHA-256 ${snapshot.sourceSha256}; expected ${manifest.source.snapshot_sha256}`);
}

const commitCount = Number(git(["rev-list", "--count", "HEAD"]).toString("utf8").trim());
if (commitCount !== manifest.source.commit_count) {
  fail(`history has ${commitCount} commits; expected ${manifest.source.commit_count}`);
}

process.stdout.write(`${JSON.stringify({
  passed: true,
  schema: manifest.schema,
  artifacts: snapshot.artifacts.length,
  sourceFiles: snapshot.sourceFiles,
  sourceSha256: snapshot.sourceSha256,
  commitCount,
  trackedTreeClean: true,
  byteSource: "git-index-blobs",
}, null, 2)}\n`);
