import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  assertCompatibleTiming,
  assertReleaseLoudness,
  buildFfmpegArguments,
  durationFromPacketCsv,
  parseArguments,
  parseLoudnormAnalysis,
  validateSrtCaptions,
} from "../scripts/finalize-demo-media.mjs";
import { DEMO_RUNTIME_BOUNDS_MS } from "../scripts/demo-storyboard.mjs";

const projectRoot = path.resolve(import.meta.dirname, "..");

function seconds(value) {
  const match = /^(\d{2}):(\d{2}):(\d{2}),(\d{3})$/.exec(value);
  assert.ok(match, `invalid SRT timestamp ${value}`);
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
}

function normalizeWords(value) {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

test("final media arguments require the three release paths", () => {
  assert.deepEqual(parseArguments([
    "--video", "silent.webm",
    "--audio", "voice.wav",
    "--output", "narrated.webm",
  ]), {
    video: "silent.webm",
    audio: "voice.wav",
    output: "narrated.webm",
  });
  assert.throws(() => parseArguments(["--video", "silent.webm"]), /--audio is required/);
  assert.throws(() => parseArguments(["--mystery", "value"]), /Unknown argument/);
});

test("narration may be padded but cannot overrun the video", () => {
  assert.doesNotThrow(() => assertCompatibleTiming(62.891, 60));
  assert.doesNotThrow(() => assertCompatibleTiming(62.891, 62.891));
  assert.throws(() => assertCompatibleTiming(62.891, 62.892), /shorten the narration/);
  assert.throws(() => assertCompatibleTiming(62.891, 64), /shorten the narration/);
});

test("caption timing is sequential and cannot overrun the video", () => {
  const captions = "1\n00:00:00,000 --> 00:00:01,000\nOpening\n\n2\n00:00:01,000 --> 00:00:02,000\nClosing\n";
  assert.deepEqual(validateSrtCaptions(captions, 2), { cueCount: 2, endsAtSeconds: 2 });
  assert.throws(() => validateSrtCaptions(captions, 1.999), /Captions end/);
  assert.throws(() => validateSrtCaptions("1\n00:00:01,000 --> 00:00:00,500\nBad\n", 2), /positive duration/);
});

test("packet timestamps recover duration when WebM metadata omits it", () => {
  assert.equal(durationFromPacketCsv("0.000000,0.033000\n62.858000,0.033000\n"), 62.891);
  assert.ok(Number.isNaN(durationFromPacketCsv("N/A,N/A\n")));
});

test("mux preserves video and produces normalized Opus narration", () => {
  const args = buildFfmpegArguments({
    video: "silent.webm",
    audio: "voice.wav",
    output: "narrated.webm",
    videoDuration: 62.891,
  });
  assert.ok(args.includes("-n"));
  assert.deepEqual(args.slice(args.indexOf("-c:v"), args.indexOf("-c:v") + 2), ["-c:v", "copy"]);
  assert.deepEqual(args.slice(args.indexOf("-c:a"), args.indexOf("-c:a") + 2), ["-c:a", "libopus"]);
  assert.ok(args.some((argument) => argument.includes("loudnorm=I=-16:TP=-1.5:LRA=11,apad[aout]")));
  assert.ok(args.includes("62.891"));
  assert.equal(args.at(-1), "narrated.webm");
});

test("post-encode loudness is parsed and release-gated", () => {
  const measurement = parseLoudnormAnalysis(`
[Parsed_loudnorm_0 @ 000001] {
  "input_i" : "-16.08",
  "input_tp" : "-1.44",
  "input_lra" : "2.10",
  "input_thresh" : "-26.18"
}
  `);
  assert.deepEqual(measurement, {
    integratedLufs: -16.08,
    truePeakDbfs: -1.44,
    loudnessRangeLu: 2.1,
  });
  assert.doesNotThrow(() => assertReleaseLoudness(measurement));
  assert.throws(() => assertReleaseLoudness({ ...measurement, integratedLufs: -13.5 }), /expected -16/);
  assert.throws(() => assertReleaseLoudness({ ...measurement, truePeakDbfs: -0.4 }), /expected at most -1/);
  assert.throws(() => parseLoudnormAnalysis("no summary"), /did not return/);
});

test("captions cover the complete voiceover without overrunning the verified cut", async () => {
  const [srt, voiceover] = await Promise.all([
    fs.readFile(path.join(projectRoot, "docs", "curate-demo.en.srt"), "utf8"),
    fs.readFile(path.join(projectRoot, "docs", "demo-voiceover-short.md"), "utf8"),
  ]);
  const cues = srt.trim().split(/\r?\n\r?\n/).map((block, index) => {
    const [number, timing, ...lines] = block.split(/\r?\n/);
    assert.equal(Number(number), index + 1);
    const [start, end] = timing.split(" --> ").map(seconds);
    return { start, end, text: lines.join(" ") };
  });
  let previousEnd = 0;
  for (const cue of cues) {
    assert.ok(cue.start >= previousEnd, "caption cues must not overlap");
    assert.ok(cue.end > cue.start, "caption cues must have positive duration");
    assert.ok(cue.text.length > 0, "caption cues must contain text");
    previousEnd = cue.end;
  }
  const plannedDurationSeconds = 185;
  assert.ok(plannedDurationSeconds * 1000 >= DEMO_RUNTIME_BOUNDS_MS.minimum);
  assert.ok(plannedDurationSeconds * 1000 <= DEMO_RUNTIME_BOUNDS_MS.maximum);
  assert.ok(previousEnd <= plannedDurationSeconds, "captions must end within the planned demo cut");
  assert.deepEqual(validateSrtCaptions(srt, plannedDurationSeconds), { cueCount: 20, endsAtSeconds: 185 });

  const spokenParagraphs = voiceover
    .split(/\r?\n\r?\n/)
    .filter((paragraph) => paragraph && !paragraph.startsWith("#") && !paragraph.startsWith("Target read:"));
  assert.equal(
    normalizeWords(cues.map((cue) => cue.text).join(" ")),
    normalizeWords(spokenParagraphs.join(" ")),
  );
});
