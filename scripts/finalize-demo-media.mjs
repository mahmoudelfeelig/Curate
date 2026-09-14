import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const HELP = `Finalize the Curate demo with a supplied narration track.

Usage:
  node scripts/finalize-demo-media.mjs --video <silent.webm> --audio <voice.wav|mp3|m4a> --output <narrated.webm> [--captions <captions.srt>] [--report <report.json>]

FFmpeg discovery:
  Pass --ffmpeg and --ffprobe, or set CURATE_FFMPEG_PATH and CURATE_FFPROBE_PATH.
  Otherwise ffmpeg and ffprobe must be available on PATH.

The command refuses to overwrite its output, rejects narration that runs past the
video, preserves the existing video stream, normalizes voice audio to -16 LUFS,
pads trailing silence to the exact video duration, and verifies the final streams.
`;

const LOUDNESS_TARGET_LUFS = -16;
const TRUE_PEAK_TARGET_DBFS = -1.5;
const LOUDNESS_TOLERANCE_LU = 1;
const TRUE_PEAK_CEILING_DBFS = -1;

export function parseArguments(argv) {
  if (argv.includes("--help") || argv.includes("-h")) return { help: true };
  const values = {};
  const allowed = new Set(["video", "audio", "output", "captions", "report", "ffmpeg", "ffprobe"]);
  for (let index = 0; index < argv.length; index += 2) {
    const token = argv[index];
    if (!token?.startsWith("--") || !allowed.has(token.slice(2))) {
      throw new Error(`Unknown argument ${String(token)}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${token} requires a value`);
    values[token.slice(2)] = value;
  }
  for (const required of ["video", "audio", "output"]) {
    if (!values[required]) throw new Error(`--${required} is required`);
  }
  return values;
}

export function assertCompatibleTiming(videoDuration, audioDuration, toleranceSeconds = 0.35) {
  if (!Number.isFinite(videoDuration) || videoDuration <= 0) throw new Error("The video duration is invalid");
  if (!Number.isFinite(audioDuration) || audioDuration <= 0) throw new Error("The narration duration is invalid");
  if (audioDuration > videoDuration + toleranceSeconds) {
    throw new Error(`Narration is ${audioDuration.toFixed(3)}s but the video is ${videoDuration.toFixed(3)}s; shorten the narration before muxing`);
  }
}

export function buildFfmpegArguments({ video, audio, output, videoDuration }) {
  return [
    "-hide_banner",
    "-loglevel", "error",
    "-n",
    "-i", video,
    "-i", audio,
    "-filter_complex", "[1:a:0]loudnorm=I=-16:TP=-1.5:LRA=11,apad[aout]",
    "-map", "0:v:0",
    "-map", "[aout]",
    "-c:v", "copy",
    "-c:a", "libopus",
    "-b:a", "128k",
    "-t", videoDuration.toFixed(3),
    "-metadata", "title=Curate demo",
    "-metadata", "comment=Natural-language feed curation with visible before and after",
    output,
  ];
}

function run(executable, args, { capture = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      windowsHide: true,
      stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    });
    let stdout = "";
    let stderr = "";
    if (capture) {
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stdout.on("data", (chunk) => { stdout += chunk; });
      child.stderr.on("data", (chunk) => { stderr += chunk; });
    }
    child.once("error", (error) => reject(new Error(`Could not start ${executable}: ${error.message}`)));
    child.once("exit", (code, signal) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${path.basename(executable)} failed (${code ?? signal})${stderr ? `: ${stderr.trim()}` : ""}`));
    });
  });
}

async function sha256(file) {
  const bytes = await fs.readFile(file);
  return createHash("sha256").update(bytes).digest("hex");
}

export function durationFromPacketCsv(csv) {
  let duration = Number.NEGATIVE_INFINITY;
  for (const line of csv.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const [ptsText, durationText] = line.split(",");
    const pts = Number(ptsText);
    const packetDuration = Number(durationText);
    if (!Number.isFinite(pts)) continue;
    duration = Math.max(duration, pts + (Number.isFinite(packetDuration) ? packetDuration : 0));
  }
  return Number.isFinite(duration) ? duration : Number.NaN;
}

export function parseLoudnormAnalysis(stderr) {
  const blocks = String(stderr || "").match(/\{[\s\S]*?"input_i"[\s\S]*?\}/g) || [];
  const block = blocks.at(-1);
  if (!block) throw new Error("FFmpeg did not return a loudness measurement");
  const parsed = JSON.parse(block);
  const integratedLufs = Number(parsed.input_i);
  const truePeakDbfs = Number(parsed.input_tp);
  const loudnessRangeLu = Number(parsed.input_lra);
  if (![integratedLufs, truePeakDbfs, loudnessRangeLu].every(Number.isFinite)) {
    throw new Error("FFmpeg returned an invalid loudness measurement");
  }
  return { integratedLufs, truePeakDbfs, loudnessRangeLu };
}

export function assertReleaseLoudness(measurement) {
  if (Math.abs(measurement.integratedLufs - LOUDNESS_TARGET_LUFS) > LOUDNESS_TOLERANCE_LU) {
    throw new Error(`Final narration measured ${measurement.integratedLufs.toFixed(2)} LUFS; expected ${LOUDNESS_TARGET_LUFS} ± ${LOUDNESS_TOLERANCE_LU} LU`);
  }
  if (measurement.truePeakDbfs > TRUE_PEAK_CEILING_DBFS) {
    throw new Error(`Final narration true peak measured ${measurement.truePeakDbfs.toFixed(2)} dBFS; expected at most ${TRUE_PEAK_CEILING_DBFS} dBFS`);
  }
}

async function measureLoudness(ffmpeg, file) {
  const { stderr } = await run(ffmpeg, [
    "-hide_banner",
    "-nostats",
    "-i", file,
    "-map", "0:a:0",
    "-af", `loudnorm=I=${LOUDNESS_TARGET_LUFS}:TP=${TRUE_PEAK_TARGET_DBFS}:LRA=11:print_format=json`,
    "-f", "null",
    "-",
  ], { capture: true });
  return parseLoudnormAnalysis(stderr);
}

async function probePacketDuration(ffprobe, file) {
  const { stdout } = await run(ffprobe, [
    "-v", "error",
    "-show_entries", "packet=pts_time,duration_time",
    "-of", "csv=p=0",
    file,
  ], { capture: true });
  return durationFromPacketCsv(stdout);
}

async function probe(ffprobe, file) {
  const { stdout } = await run(ffprobe, [
    "-v", "error",
    "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
    "-of", "json",
    file,
  ], { capture: true });
  const result = JSON.parse(stdout);
  let duration = Number(result?.format?.duration);
  if (!Number.isFinite(duration) || duration <= 0) {
    duration = await probePacketDuration(ffprobe, file);
  }
  return {
    duration,
    streams: Array.isArray(result?.streams) ? result.streams : [],
  };
}

async function requireFile(file, label) {
  const resolved = path.resolve(file);
  const stat = await fs.stat(resolved).catch(() => null);
  if (!stat?.isFile()) throw new Error(`${label} does not exist: ${resolved}`);
  return { path: resolved, bytes: stat.size };
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  if (options.help) {
    process.stdout.write(HELP);
    return;
  }

  const video = await requireFile(options.video, "Silent video");
  const audio = await requireFile(options.audio, "Narration audio");
  const captions = options.captions ? await requireFile(options.captions, "Caption file") : null;
  const output = path.resolve(options.output);
  if (await fs.stat(output).then(() => true).catch(() => false)) {
    throw new Error(`Output already exists; refusing to overwrite ${output}`);
  }
  await fs.mkdir(path.dirname(output), { recursive: true });

  const ffmpegCandidate = options.ffmpeg || process.env.CURATE_FFMPEG_PATH;
  const ffprobeCandidate = options.ffprobe || process.env.CURATE_FFPROBE_PATH;
  const ffmpeg = ffmpegCandidate ? path.resolve(ffmpegCandidate) : "ffmpeg";
  const ffprobe = ffprobeCandidate ? path.resolve(ffprobeCandidate) : "ffprobe";
  const inputVideo = await probe(ffprobe, video.path);
  const inputAudio = await probe(ffprobe, audio.path);
  const videoStream = inputVideo.streams.find((stream) => stream.codec_type === "video");
  const audioStream = inputAudio.streams.find((stream) => stream.codec_type === "audio");
  if (!videoStream) throw new Error("The silent demo has no video stream");
  if (!audioStream) throw new Error("The narration file has no audio stream");
  assertCompatibleTiming(inputVideo.duration, inputAudio.duration);

  await run(ffmpeg, buildFfmpegArguments({
    video: video.path,
    audio: audio.path,
    output,
    videoDuration: inputVideo.duration,
  }));

  const finalMedia = await probe(ffprobe, output);
  const finalVideo = finalMedia.streams.find((stream) => stream.codec_type === "video");
  const finalAudio = finalMedia.streams.find((stream) => stream.codec_type === "audio");
  if (!finalVideo || !finalAudio) throw new Error("Final demo must contain one video stream and one audio stream");
  if (Math.abs(finalMedia.duration - inputVideo.duration) > 0.35) {
    throw new Error(`Final duration ${finalMedia.duration.toFixed(3)}s does not match the source ${inputVideo.duration.toFixed(3)}s`);
  }
  if (finalVideo.width !== videoStream.width || finalVideo.height !== videoStream.height) {
    throw new Error("Final video dimensions changed during muxing");
  }
  const loudness = await measureLoudness(ffmpeg, output);
  assertReleaseLoudness(loudness);

  const outputStat = await fs.stat(output);
  const reportPath = path.resolve(options.report || `${output}.report.json`);
  const report = {
    schema: "curate/narrated-demo-release/v1",
    generated_at: new Date().toISOString(),
    source_video: {
      bytes: video.bytes,
      sha256: await sha256(video.path),
      duration_seconds: inputVideo.duration,
      codec: videoStream.codec_name,
      width: videoStream.width,
      height: videoStream.height,
    },
    narration: {
      bytes: audio.bytes,
      sha256: await sha256(audio.path),
      duration_seconds: inputAudio.duration,
      codec: audioStream.codec_name,
      target_lufs: LOUDNESS_TARGET_LUFS,
      target_true_peak_dbfs: TRUE_PEAK_TARGET_DBFS,
    },
    captions: captions ? {
      bytes: captions.bytes,
      sha256: await sha256(captions.path),
      format: path.extname(captions.path).slice(1).toLowerCase(),
    } : null,
    output: {
      path: output,
      bytes: outputStat.size,
      sha256: await sha256(output),
      duration_seconds: finalMedia.duration,
      video_codec: finalVideo.codec_name,
      audio_codec: finalAudio.codec_name,
      width: finalVideo.width,
      height: finalVideo.height,
      measured_lufs: loudness.integratedLufs,
      measured_true_peak_dbfs: loudness.truePeakDbfs,
      measured_loudness_range_lu: loudness.loudnessRangeLu,
      loudness_tolerance_lu: LOUDNESS_TOLERANCE_LU,
      true_peak_ceiling_dbfs: TRUE_PEAK_CEILING_DBFS,
    },
    passed: true,
  };
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  process.stdout.write(`${JSON.stringify({ passed: true, output, report: reportPath }, null, 2)}\n`);
}

const invokedDirectly = process.argv[1]
  && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (invokedDirectly) {
  main().catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
