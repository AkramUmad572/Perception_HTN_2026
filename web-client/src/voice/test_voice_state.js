/**
 * Tests for the hold-to-talk state machine.
 *
 * Run with: node web-client/src/voice/test_voice_state.js
 *
 * These cover the ways Percy used to get stuck in `listening` — the red ring
 * in XR, the red mic button on desktop — with the prompt never sent and no
 * way out but a page reload:
 *
 * 1. Barging in on a spoken reply (pause fires neither onended nor onerror)
 * 2. Talking while a background build/search job is still polling
 * 3. A tap released before getUserMedia has finished opening the mic
 * 4. A slow pinch release sampled inside the hysteresis dead band
 *
 * The first three drive the real PercyAssistant with the browser bits stubbed.
 * The fourth replays main.js's pollHand edge logic, which has no DOM in it.
 *
 * The last group covers the opposite failure: Percy going quiet. A tracked
 * hand emits BOTH the select events main.js binds on the controller and the
 * joint gaps pollHand samples, so push-to-talk had two drivers and a single
 * jittered frame could barge in and drop a reply mid-sentence.
 */

import { PercyAssistant } from "./PercyAssistant.js";
import { voiceState } from "./VoiceState.js";
import { selectEventDrivesPtt } from "../interaction/pttSource.js";

const assert = (condition, message) => {
  if (!condition) {
    throw new Error(`ASSERTION FAILED: ${message}`);
  }
};

const testResults = { passed: 0, failed: 0 };

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    testResults.passed++;
  } catch (e) {
    console.log(`  ✗ ${name}`);
    console.log(`      ${e.message}`);
    testResults.failed++;
  }
}

// --- browser stubs --------------------------------------------------------

/** Enough of HTMLAudioElement to be interrupted: pause() fires only 'pause'. */
class FakeAudio {
  constructor(src) {
    this.src = src;
    this.onended = null;
    this.onerror = null;
    this.onpause = null;
  }
  play() {
    return Promise.resolve();
  }
  pause() {
    this.onpause?.();
  }
  /** The reply finishes on its own. */
  finish() {
    this.onended?.();
  }
}

globalThis.Audio = FakeAudio;
globalThis.navigator = globalThis.navigator || { mediaDevices: {} };

const AUDIO_REPLY = {
  ok: true,
  reply: "here you go",
  reply_audio_url: "/media/reply.mp3",
  action: "chat",
};

/** A Percy with a stub recorder and server, and its own clean state. */
function makePercy(overrides = {}) {
  voiceState.setMuted(false);
  voiceState.toIdle();

  const percy = new PercyAssistant({
    onStatusMessage: () => {},
    onModelUpdate: () => {},
    ...overrides,
  });
  percy.started = true;
  percy.recorder.start = async () => {};
  percy.recorder.stop = async () => ({ type: "audio/webm", size: 4096 });
  percy.recorder.abort = () => {};
  percy._sendVoice = async () => AUDIO_REPLY;
  return percy;
}

/** Let queued microtasks and 0ms timers drain. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

/** One full hold-and-release. */
async function say(percy) {
  await percy.beginTalk();
  await percy.endTalk();
  await settle();
}

console.log("=".repeat(60));
console.log("HOLD-TO-TALK STATE MACHINE TESTS");
console.log("(a stuck 'listening' is a red indicator that never clears)");
console.log("=".repeat(60));

console.log("\n=== Barge-in on a spoken reply ===");

await test("interrupting a reply does not wedge endTalk", async () => {
  const percy = makePercy();
  await percy.beginTalk();
  percy.endTalk(); // not awaited: the reply is still playing
  await settle();
  assert(voiceState.state === "speaking", `reply playing, got "${voiceState.state}"`);

  await percy.beginTalk(); // barge in: pauses the reply audio
  assert(voiceState.state === "listening", `holding, got "${voiceState.state}"`);

  let sent = false;
  const send = percy._sendVoice;
  percy._sendVoice = async (...args) => {
    sent = true;
    return send(...args);
  };

  percy.endTalk(); // release: this prompt must be sent
  await settle();
  assert(sent, "the release was swallowed and the prompt never left the headset");
  assert(
    voiceState.state !== "listening",
    "stuck in listening — the mic was left live with nothing to end it"
  );
});

await test("a paused reply releases the turn instead of hanging", async () => {
  const percy = makePercy();
  await percy.beginTalk();
  const turn = percy.endTalk();
  await settle();

  await percy.beginTalk(); // pauses the reply mid-sentence
  await Promise.race([
    turn,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("endTalk never settled after pause()")), 500)
    ),
  ]);
  percy.cancelTalk(); // drop the hold; this test is about `turn` settling
  await settle();
});

await test("an uninterrupted reply still returns to idle", async () => {
  const percy = makePercy();
  let audio = null;
  const realAudio = globalThis.Audio;
  globalThis.Audio = class extends FakeAudio {
    constructor(src) {
      super(src);
      audio = this;
    }
  };
  await percy.beginTalk();
  const turn = percy.endTalk();
  await settle();
  assert(voiceState.state === "speaking", `reply playing, got "${voiceState.state}"`);
  audio.finish();
  await turn;
  globalThis.Audio = realAudio;
  assert(voiceState.state === "idle", `after the reply, got "${voiceState.state}"`);
});

console.log("\n=== Talking while a job is still polling ===");

await test("a prompt during a running build is still sent", async () => {
  const percy = makePercy();
  percy._sendVoice = async () => ({
    ok: true,
    reply: "building",
    action: "building",
    job_id: "job-1",
  });
  percy._awaitJob = () => new Promise(() => {}); // build runs for minutes

  await percy.beginTalk();
  percy.endTalk(); // starts the build, then polls
  await settle();

  percy._sendVoice = async () => ({ ok: true, reply: "sure", action: "chat" });
  let sent = false;
  const send = percy._sendVoice;
  percy._sendVoice = async (...args) => {
    sent = true;
    return send(...args);
  };

  await say(percy); // ask something while the build runs
  assert(sent, "the release was swallowed while the job was polling");
  assert(voiceState.state !== "listening", `stuck in "${voiceState.state}"`);
});

await test("a stale build response does not clobber a fresh recording", async () => {
  const percy = makePercy();
  let resolveJob;
  percy._sendVoice = async () => ({
    ok: true,
    reply: "building",
    action: "building",
    job_id: "job-stale",
  });
  percy._awaitJob = () => new Promise((resolve) => (resolveJob = resolve));

  await percy.beginTalk();
  const deliver = percy.endTalk(); // starts the stale build; _awaitJob never resolves yet
  await settle();

  // The user gives up and resets — main.js's resetEverything() bumps the
  // turn counter this same way — then starts a brand new recording.
  percy._nextTurn();
  await percy.beginTalk();
  assert(voiceState.state === "listening", "the fresh recording never started listening");

  // NOW the stale build finally resolves and lands, with a spoken reply.
  resolveJob({ ok: true, reply: "finished the stale model", action: "generate" });
  await deliver;
  await settle();

  assert(
    voiceState.state === "listening",
    `the stale build response clobbered the active recording — state is now "${voiceState.state}"`
  );

  voiceState.toIdle(); // done with the still-open recording; no network round trip needed
});

console.log("\n=== Release before the mic is open ===");

await test("a tap released during getUserMedia does not strand the mic", async () => {
  const percy = makePercy();
  let openMic;
  percy.recorder.start = () => new Promise((resolve) => (openMic = resolve));
  percy.recorder.stop = async () => null; // too short to have recorded anything

  const pressed = percy.beginTalk(); // mic still opening
  await percy.endTalk(); // released early
  openMic(); // mic opens after the release
  await pressed;
  await settle();

  assert(
    voiceState.state !== "listening",
    "stuck in listening — the mic opened after the release and nobody ended it"
  );
});

await test("a normal hold still records and sends", async () => {
  const percy = makePercy();
  let sent = false;
  percy._sendVoice = async () => {
    sent = true;
    return { ok: true, reply: "sure", action: "chat" };
  };
  await percy.beginTalk();
  assert(voiceState.state === "listening", `holding, got "${voiceState.state}"`);
  await percy.endTalk();
  await settle();
  assert(sent, "a normal hold-and-release must send the utterance");
  assert(voiceState.state === "idle", `after the reply, got "${voiceState.state}"`);
});

await test("cancelTalk still drops the hold without sending", async () => {
  const percy = makePercy();
  let sent = false;
  percy._sendVoice = async () => {
    sent = true;
    return { ok: true, reply: "sure", action: "chat" };
  };
  await percy.beginTalk();
  percy.cancelTalk(); // resolved as half of a two-hand gesture
  await settle();
  assert(!sent, "a cancelled hold must not be sent");
  assert(voiceState.state === "idle", `after cancel, got "${voiceState.state}"`);
});

console.log("\n=== Pinch release edge detection (main.js pollHand) ===");

// The PTT edge logic from main.js, replayed over a release. `held` is the
// hysteresis latch; the question is whether the release is ever emitted.
const PINCH_THRESHOLD_START = 0.032;
const PINCH_THRESHOLD_END = 0.045;

function replayPinch(gaps) {
  let held = false;
  let wasHeld = false;
  let begins = 0;
  let ends = 0;
  for (const gap of gaps) {
    wasHeld = held;
    if (gap < PINCH_THRESHOLD_START) held = true;
    else if (gap > PINCH_THRESHOLD_END) held = false;
    if (held && !wasHeld) begins++;
    if (!held && wasHeld) ends++;
  }
  return { begins, ends, held };
}

await test("a fast release ends the hold", () => {
  const { begins, ends } = replayPinch([0.02, 0.02, 0.06, 0.07]);
  assert(begins === 1, `one beginTalk, got ${begins}`);
  assert(ends === 1, `one endTalk, got ${ends}`);
});

await test("a slow release through the dead band still ends the hold", () => {
  const { begins, ends } = replayPinch([0.02, 0.02, 0.038, 0.06, 0.07]);
  assert(begins === 1, `one beginTalk, got ${begins}`);
  assert(ends === 1, `a release sampled at 38mm was dropped, got ${ends} endTalk`);
});

await test("hovering in the dead band does not start a hold", () => {
  const { begins, ends } = replayPinch([0.06, 0.038, 0.04, 0.038, 0.06]);
  assert(begins === 0, `no beginTalk, got ${begins}`);
  assert(ends === 0, `no endTalk, got ${ends}`);
});

await test("a held pinch stays held while it jitters inside the dead band", () => {
  const { begins, ends, held } = replayPinch([0.02, 0.038, 0.02, 0.038, 0.02]);
  assert(begins === 1, `one beginTalk, got ${begins}`);
  assert(ends === 0, `jitter must not end the hold, got ${ends} endTalk`);
  assert(held, "still holding at the end");
});

console.log("\n=== Left-hand PTT must have exactly one driver ===");

await test("a tracked hand leaves the select events alone", () => {
  const trackedHand = { joints: { "index-finger-tip": {}, "thumb-tip": {}, wrist: {} } };
  assert(
    !selectEventDrivesPtt(trackedHand),
    "pollHand already latches a tracked hand's pinch — taking the select event too " +
      "gives push-to-talk a second, hysteresis-free driver"
  );
});

await test("a controller still drives PTT through the select events", () => {
  assert(selectEventDrivesPtt({ joints: {} }), "a controller has no hand joints to sample");
  assert(selectEventDrivesPtt(undefined), "no hand object at all is still a controller");
});

/**
 * main.js's left-hand wiring, with the DOM taken out: pollHand's hysteresis
 * latch plus the controller select listeners, both pointed at one Percy.
 * A frame is { gap, select } — `gap` is the thumb/index distance (null when
 * the hand is not tracked) and `select` is what the XR runtime's own pinch
 * detector emitted that frame. The latch persists across calls, the way it
 * does across render frames in main.js.
 */
function leftHandDriver(percy) {
  const PINCH_THRESHOLD_START = 0.032;
  const PINCH_THRESHOLD_END = 0.045;
  let held = false;

  return function feed(...frames) {
    for (const { gap, select } of frames) {
      const hand = gap === null ? { joints: {} } : { joints: { "index-finger-tip": {} } };

      // left.controller select listeners (main.js)
      if (select === "start" && selectEventDrivesPtt(hand)) percy.beginTalk();
      if (select === "end" && selectEventDrivesPtt(hand)) percy.endTalk();

      // pollHand (main.js)
      const wasHeld = held;
      if (gap === null) held = false;
      else if (gap < PINCH_THRESHOLD_START) held = true;
      else if (gap > PINCH_THRESHOLD_END) held = false;
      if (held && !wasHeld) percy.beginTalk();
      else if (!held && wasHeld) percy.endTalk();
    }
  };
}

await test("a pinch blip during a reply does not cut Percy off", async () => {
  const percy = makePercy();
  await percy.beginTalk();
  percy.endTalk();
  await settle();
  assert(voiceState.state === "speaking", `reply playing, got "${voiceState.state}"`);
  const playing = percy.replyAudio;
  assert(playing, "a reply with audio should be holding an Audio element");

  // The hand is open the whole time — the user is listening, not talking.
  // The runtime's pinch detector fires anyway on one occluded frame.
  leftHandDriver(percy)(
    { gap: 0.07, select: null },
    { gap: 0.07, select: "start" },
    { gap: 0.07, select: "end" },
    { gap: 0.07, select: null }
  );
  await settle();

  assert(
    percy.replyAudio === playing,
    "a stray select event paused the reply and dropped it — this is Percy going " +
      "quiet mid-sentence with nothing in the log"
  );
  assert(
    voiceState.state === "speaking",
    `the reply should still be playing, got "${voiceState.state}"`
  );
});

await test("a real hand pinch still holds and sends, once", async () => {
  const percy = makePercy();
  let sent = 0;
  percy._sendVoice = async () => {
    sent++;
    return { ok: true, reply: "sure", action: "chat" };
  };

  // A real pinch: the runtime sees it too, so both drivers fire.
  const feed = leftHandDriver(percy);
  feed({ gap: 0.07, select: null }, { gap: 0.02, select: "start" });
  await settle();
  assert(voiceState.state === "listening", `holding, got "${voiceState.state}"`);

  feed({ gap: 0.07, select: "end" });
  await settle();
  assert(sent === 1, `one utterance per hold, got ${sent}`);
});

await test("a controller trigger still holds and sends", async () => {
  const percy = makePercy();
  let sent = 0;
  percy._sendVoice = async () => {
    sent++;
    return { ok: true, reply: "sure", action: "chat" };
  };

  // No hand joints: gap is null every frame, so pollHand never latches and
  // the select events are the only driver left.
  const feed = leftHandDriver(percy);
  feed({ gap: null, select: "start" });
  await settle();
  assert(voiceState.state === "listening", `trigger held, got "${voiceState.state}"`);

  feed({ gap: null, select: "end" });
  await settle();
  assert(sent === 1, `one utterance per trigger pull, got ${sent}`);
});

await test("barging in on purpose still interrupts the reply", async () => {
  const percy = makePercy();
  await percy.beginTalk();
  percy.endTalk();
  await settle();
  assert(voiceState.state === "speaking", `reply playing, got "${voiceState.state}"`);

  // An actual pinch, not a blip: this one is allowed to talk over Percy.
  leftHandDriver(percy)({ gap: 0.02, select: "start" });
  await settle();
  assert(voiceState.state === "listening", `barge-in should take the mic, got "${voiceState.state}"`);
  assert(percy.replyAudio === null, "a real barge-in drops the reply audio");
});

// Summary
console.log("\n" + "=".repeat(60));
console.log("SUMMARY");
console.log("=".repeat(60));
console.log(`TOTAL: ${testResults.passed}/${testResults.passed + testResults.failed} passed`);

if (testResults.failed > 0) {
  console.log(`\n⚠️  ${testResults.failed} TESTS FAILED`);
  process.exit(1);
} else {
  console.log("\n✓ ALL TESTS PASSED");
  process.exit(0);
}
