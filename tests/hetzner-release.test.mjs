import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("Hetzner release is bound to the renamed public repository and immutable gateway", async () => {
  const manifest = JSON.parse(await read("../.github/hetzner-release.json"));
  const workflow = await read("../.github/workflows/deploy-production.yml");
  assert.equal(manifest.id, "curate");
  assert.equal(manifest.source.repository, "mahmoudelfeelig/curate");
  assert.deepEqual(manifest.source.required_workflows, ["Curate CI"]);
  assert.deepEqual(manifest.release.components.map(({ name }) => name), ["api", "oauth", "tunnel"]);
  for (const component of manifest.release.components) {
    assert.ok(component.dockerfile.includes("/"), `${component.name} Dockerfile must be source-root relative`);
    await read(`../${component.dockerfile}`);
  }
  assert.match(workflow, /HetznerReleaseGateway\/.github\/workflows\/release\.yml@[0-9a-f]{40}/);
  assert.doesNotMatch(workflow, /secrets\./);
});

test("tunnel exposes only the Curate API and the public AT Protocol boundary", async () => {
  const config = await read("../deploy/hetzner/tunnel.yml");
  assert.match(config, /service: http:\/\/curate-api:8000/);
  assert.match(config, /service: http:\/\/curate-oauth:4310/);
  assert.match(config, /path: \^\/oauth\/atproto\//);
  assert.match(config, /service: http_status:404/);
  assert.doesNotMatch(config, /host\.docker\.internal|127\.0\.0\.1/);
});
