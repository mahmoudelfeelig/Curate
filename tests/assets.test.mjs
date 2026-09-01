import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const projectRoot = path.resolve(import.meta.dirname, "..");
const publicRoot = path.join(projectRoot, "public");

async function walk(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(entryPath));
    else files.push(entryPath);
  }
  return files;
}

test("every shipped visual and font has a pinned notice and digest", async () => {
  const notices = (await fs.readFile(path.join(projectRoot, "THIRD_PARTY_NOTICES.md"), "utf8")).toUpperCase();
  const publicFiles = await walk(publicRoot);
  const shippedAssets = publicFiles.filter((file) => !file.includes(`${path.sep}licenses${path.sep}`));
  assert.ok(shippedAssets.length > 0);

  for (const file of shippedAssets) {
    const relative = path.relative(projectRoot, file).replaceAll("\\", "/");
    const digest = createHash("sha256").update(await fs.readFile(file)).digest("hex").toUpperCase();
    assert.match(notices, new RegExp(relative.split("/").at(-1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), `${relative} is absent from THIRD_PARTY_NOTICES.md`);
    assert.ok(notices.includes(digest), `${relative} has no matching SHA-256 in THIRD_PARTY_NOTICES.md`);
  }
});

test("the shipping tree contains only the declared licensed asset families", async () => {
  const publicFiles = (await walk(publicRoot)).map((file) => path.relative(projectRoot, file).replaceAll("\\", "/"));
  const visualFiles = publicFiles.filter((file) => /\.(?:avif|gif|jpe?g|png|svg|webp)$/i.test(file));
  const rasterFiles = visualFiles.filter((file) => !file.endsWith(".svg"));

  assert.deepEqual(rasterFiles.sort(), [
    "public/assets/licensed/paper006-color-1k.jpg",
    "public/assets/licensed/rough-linen-1k.jpg",
  ]);
  assert.ok(visualFiles.every((file) => file.startsWith("public/assets/licensed/")));
  assert.ok(publicFiles.filter((file) => /\.(?:otf|ttf|woff2?)$/i.test(file)).every((file) => file.startsWith("public/fonts/")));

  const shippingSources = [
    path.join(projectRoot, "index.html"),
    ...await walk(path.join(projectRoot, "src")),
    ...await walk(path.join(projectRoot, "worker")),
  ];
  const shippingText = (await Promise.all(shippingSources.map(async (file) => {
    try {
      return await fs.readFile(file, "utf8");
    } catch {
      return "";
    }
  }))).join("\n");
  assert.doesNotMatch(shippingText, /ai-generated|unprovenanced/i);
});

test("the documentation tree contains no raster design source", async () => {
  const documentationFiles = (await walk(path.join(projectRoot, "docs")))
    .map((file) => path.relative(projectRoot, file).replaceAll("\\", "/"));
  assert.deepEqual(
    documentationFiles.filter((file) => /\.(?:avif|gif|jpe?g|png|webp)$/i.test(file)),
    [],
  );
});
