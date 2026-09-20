/**
 * PercyAssistant - Hold-to-talk coordinator
 *
 * beginTalk (hold) → record → endTalk (release) → /api/voice → CAD/TTS
 */

import { voiceState } from "./VoiceState.js";
import { PTTRecorder } from "./PTTRecorder.js";
import { parseRegionCommand } from "../interaction/regionOps.js";
import { needsSelection } from "../interaction/partEdit.js";

const API_BASE = "";
const SESSION_ID = "default";
const READY_HINT =
  "Hold left trigger / pinch to talk. Right pinch the model to move it.";

// Kept under the proxy's own limit so a stall surfaces here, with a message,
// rather than as a severed connection.
const REQUEST_TIMEOUT_MS = 180000;
const JOB_POLL_MS = 800;
const JOB_MAX_MS = 600000;
// Polls are cheap, so ride out a few dropped ones before giving up on a build.
const JOB_MAX_MISSES = 5;

// Cached once granted so grounded chat answers ("how's the weather") can be
// accurate for the user's real location. Requested lazily on first talk —
// never blocks startup, and a denial just leaves this null (backend
// degrades gracefully with no location).
let _cachedLocation = null;
let _locationRequested = false;

function _requestLocationOnce() {
  if (_locationRequested || _cachedLocation) return;
  _locationRequested = true;
  if (!("geolocation" in navigator)) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      _cachedLocation = { lat: pos.coords.latitude, lon: pos.coords.longitude };
    },
    (err) => {
      console.info("[Percy] Geolocation unavailable/denied:", err.message);
    },
    { timeout: 8000, maximumAge: 600000 }
  );
}

export class PercyAssistant {
  constructor(options = {}) {
    this.onModelUpdate = options.onModelUpdate || (() => {});
    this.onStatusMessage = options.onStatusMessage || (() => {});
    this.onTalkingChange = options.onTalkingChange || (() => {});
    this.onPhotoCandidates = options.onPhotoCandidates || (() => {});
    // Applies a parsed region command (regionOps.js) to the loaded model and
    // saves the result as a new version. When set, a text/voice command that
    // matches the local table runs here instead of /api/command — no LLM
    // round trip for "bigger" / "pull it out" / etc. on a selection.
    this.onRegionCommand = options.onRegionCommand || null;
    // Applies a dimensional edit to the selected CAD part by rewriting its
    // PARAMS value (interaction/partEdit.js). Returns null when it cannot
    // resolve the utterance to a real parameter, in which case the command
    // falls through to /api/command exactly as before. ~67 ms vs ~15 s for
    // the same edit through codegen.
    this.onPartEdit = options.onPartEdit || null;
    // Captures the current view with the selection circled and uploads it for
    // a semantic sculpt edit (WS-H). The router answers action="semantic_edit"
    // with rebuilt=false because the server has no picture yet; the headset is
    // what takes it.
    this.onSemanticEdit = options.onSemanticEdit || null;
    this.onJobProgress = options.onJobProgress || (() => {});
    this.onItems = options.onItems || (() => {});

    this.recorder = new PTTRecorder();
    this.recorder.onMaxHold = () => this.endTalk();
    this.replyAudio = null;
    this.started = false;
    this._ending = false;
    // What the user last pointed at (interaction/selection.js). Consumed by
    // the next voice or text command, then cleared.
    this.selection = null;
    // Called once the pending selection is consumed (sent or dropped), so
    // main.js can clear the in-world highlight.
    this.onSelectionCleared = options.onSelectionCleared || (() => {});
  }

  /** Attach a Selection to the next voice/text command; cleared once sent. */
  setSelection(selection) {
    this.selection = selection || null;
  }

  clearSelection() {
    if (!this.selection) return;
    this.selection = null;
    this.onSelectionCleared();
  }

  _takeSelection() {
    const selection = this.selection;
    if (selection) {
      this.selection = null;
      this.onSelectionCleared();
    }
    return selection;
  }

  async start() {
    if (this.started) return;
    this.started = true;
    voiceState.toIdle();
    this.onStatusMessage(`Percy ready. ${READY_HINT}`, true);
    try {
      await this.recorder.ensureMic();
    } catch (e) {
      console.error("[Percy] Mic permission failed:", e);
      voiceState.toError("Microphone access required");
      this.onStatusMessage("Mic access required. Allow and refresh.", false);
    }
  }

  stop() {
    this.recorder.releaseMic();
    this.started = false;
    voiceState.toIdle();
    this.onTalkingChange(false);
  }

  toggleMute() {
    const muted = voiceState.toggleMute();
    if (muted) {
      this.recorder.abort();
      this.onTalkingChange(false);
      this.onStatusMessage("Percy muted", true);
    } else {
      this.onStatusMessage(`Percy unmuted. ${READY_HINT}`, true);
    }
    return muted;
  }

  async greet() {
    if (voiceState.isMuted) return;
    try {
      const localHour = new Date().getHours();
      const data = await this._fetchJson(
        `${API_BASE}/api/greet?session_id=${SESSION_ID}&local_hour=${localHour}`,
        {},
        15000
      );
      // The greeting is fire-and-forget, so by the time it lands the user may
      // already have grabbed the mic. Speaking over them — and then dropping
      // the state machine back to idle underneath a live recording — is worse
      // than skipping the greeting, so the mic always wins.
      if (voiceState.isListening || voiceState.isThinking) return;
      if (data.reply_audio_url) {
        voiceState.toSpeaking();
        this.onStatusMessage(data.reply, true);
        const audio = new Audio(data.reply_audio_url);
        this.replyAudio = audio;
        try {
          await new Promise((resolve) => {
            audio.onended = resolve;
            audio.onerror = resolve;
            // beginTalk() pauses the reply audio to make room for the user;
            // pause fires neither onended nor onerror, so watch for it here
            // or this promise never settles.
            audio.onpause = resolve;
            audio.play().catch(resolve);
          });
        } catch (_) {}
        if (this.replyAudio === audio) this.replyAudio = null;
        // Only hand back a state we still own: the user may have started
        // talking while the greeting was playing.
        if (voiceState.isSpeaking) voiceState.toIdle();
      } else {
        this.onStatusMessage(data.reply, true);
      }
    } catch (e) {
      console.warn("[Percy] Greeting failed:", e);
      // Non-fatal — silently skip the greeting rather than blocking AR entry.
    }
  }

  async beginTalk() {
    if (!this.started || voiceState.isMuted) return;
    if (voiceState.isListening || voiceState.isThinking) return;
    _requestLocationOnce();
    this._cancelPending = false;

    if (this.replyAudio) {
      try {
        this.replyAudio.pause();
      } catch (_) {}
      this.replyAudio = null;
    }

    try {
      await this.recorder.start();
      if (this._cancelPending) {
        // cancelTalk() arrived while the mic was still opening.
        this._cancelPending = false;
        this.recorder.abort();
        this.onTalkingChange(false);
        return;
      }
      voiceState.toListening();
      this.onTalkingChange(true);
      this.onStatusMessage("Listening… hold to talk, release to send.", true);
    } catch (e) {
      console.error("[Percy] Failed to start recording:", e);
      voiceState.toError("Microphone access required");
      this.onStatusMessage("Mic access required. Allow and retry.", false);
    }
  }

  /**
   * Drop the current hold without sending it. Used when a left-hand pinch
   * turns out to be half of a two-hand gesture rather than push-to-talk.
   */
  cancelTalk() {
    this._cancelPending = true;
    if (!voiceState.isListening || this._ending) return;
    this._cancelPending = false;
    this.recorder.abort();
    voiceState.toIdle();
    this.onTalkingChange(false);
  }

  async endTalk() {
    if (!voiceState.isListening || this._ending) return;
    this._ending = true;
    this.onTalkingChange(false);

    try {
      const audioBlob = await this.recorder.stop();
      if (!audioBlob) {
        voiceState.toIdle();
        this.onStatusMessage(`Hold a bit longer, then release. ${READY_HINT}`, true);
        return;
      }

      voiceState.toThinking();
      this.onStatusMessage("Looking that up…", true);

      const result = await this._sendVoice(audioBlob);
      await this._deliverResponse(result);
    } catch (e) {
      console.error("[Percy] Error in voice pipeline:", e);
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
    } finally {
      this._ending = false;
    }
  }

  async _fetchJson(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  _postJson(url, body, timeoutMs) {
    return this._fetchJson(
      url,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutMs
    );
  }

  async _sendVoice(blob) {
    const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("audio", blob, `utterance.${ext}`);
    form.append("session_id", SESSION_ID);
    const selection = this._takeSelection();
    if (selection) form.append("selection", JSON.stringify(selection));
    if (_cachedLocation) {
      form.append("lat", String(_cachedLocation.lat));
      form.append("lon", String(_cachedLocation.lon));
    }

    return this._fetchJson(`${API_BASE}/api/voice`, { method: "POST", body: form });
  }

  async sendTextCommand(text) {
    if (voiceState.isMuted) return null;
    _requestLocationOnce();

    // A region edit on an active selection runs client-side (regionOps.js),
    // with no LLM round trip: parse the small local table first.
    const regionCmd = this.selection ? parseRegionCommand(text) : null;
    if (regionCmd && this.onRegionCommand) {
      const selection = this._takeSelection();
      voiceState.toThinking();
      this.onStatusMessage(`Applying "${text}"…`, true);
      try {
        const result = await this.onRegionCommand(regionCmd, selection, text);
        await this._handleResponse(result);
        return result;
      } catch (e) {
        voiceState.toError(e.message);
        this.onStatusMessage(`Error: ${e.message}`, false);
        setTimeout(() => voiceState.toIdle(), 3000);
        return null;
      }
    }

    // A dimensional edit on a selected CAD part is a PARAMS rewrite, not a
    // codegen round trip. onPartEdit returns null when it cannot resolve the
    // utterance to a real parameter, and we fall through to the server.
    if (this.selection && this.onPartEdit) {
      const selection = this.selection;
      const pending = await this.onPartEdit(text, selection);
      if (pending) {
        this._takeSelection();
        await this._handleResponse(pending);
        return pending;
      }
    }

    // "bigger" / "make it longer" only mean something against a part. With no
    // selection these used to fall through to the LLM and quietly do something
    // else, which is the failure mode this guard exists to stop.
    if (!this.selection && needsSelection(text)) {
      this.onStatusMessage("Point at a part first, then say that again.", false);
      voiceState.toIdle();
      return null;
    }

    voiceState.toThinking();
    this.onStatusMessage(`Looking that up… ("${text}")`, true);

    try {
      const body = { text, session_id: SESSION_ID };
      const selection = this._takeSelection();
      if (selection) body.selection = selection;
      if (_cachedLocation) {
        body.lat = _cachedLocation.lat;
        body.lon = _cachedLocation.lon;
      }
      const result = await this._postJson(`${API_BASE}/api/command`, body);

      // The router says this is a semantic edit but has no picture yet: the
      // headset renders the current view with the selection circled and posts
      // it to /semantic_edit, which answers with a job to poll.
      if (result?.action === "semantic_edit" && !result?.rebuilt
          && selection && this.onSemanticEdit) {
        await this._handleResponse(result);
        const started = await this.onSemanticEdit(text, selection);
        if (started) return this._deliverResponse(started);
        return result;
      }

      await this._deliverResponse(result);
      return result;
    } catch (e) {
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
      return null;
    }
  }

  async choosePhoto(fileId) {
    voiceState.toThinking();
    this.onStatusMessage("Building that from the photo… this takes a bit.", true);
    try {
      try {
        const confirm = await this._postJson(
          `${API_BASE}/api/photos/confirm`,
          { file_id: fileId, session_id: SESSION_ID },
          30000
        );
        if (confirm.reply) this.onStatusMessage(confirm.reply, true);
        if (confirm.reply_audio_url) {
          voiceState.toSpeaking();
          if (this.replyAudio) {
            try { this.replyAudio.pause(); } catch (_) {}
          }
          this.replyAudio = new Audio(confirm.reply_audio_url);
          this.replyAudio.play().catch(() => {});
          this.replyAudio.onended = () => {
            if (voiceState.isSpeaking) voiceState.toThinking();
          };
        }
      } catch (_) {}

      const started = await this._postJson(
        `${API_BASE}/api/photos/choose`,
        { file_id: fileId, session_id: SESSION_ID },
        30000
      );
      const result = started.job_id ? await this._awaitJob(started.job_id) : started;
      await this._handleResponse(result);
      return result;
    } catch (e) {
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
      return null;
    }
  }

  /**
   * Drag-release from the dimension panel: rewrite one or more named PARAMS
   * on the current CAD project and rerun the sandbox. Silent on purpose —
   * this can fire on every release, like save_client_version.
   */
  async postParamUpdate(projectId, updates) {
    try {
      const result = await this._postJson(
        `${API_BASE}/api/projects/${projectId}/params`,
        { updates, session_id: SESSION_ID },
        30000
      );
      await this._handleResponse(result);
      return result;
    } catch (e) {
      console.error("[Percy] Param update failed:", e);
      this.onStatusMessage(`Error: ${e.message}`, false);
      return null;
    }
  }

  /**
   * Upload a render of the current view, with the selection circled, for a
   * semantic sculpt edit. Answers with a job_id; the whole chain is ~40-70 s.
   */
  async postSemanticEdit(projectId, blob, text) {
    const form = new FormData();
    form.append("image", blob, "view.png");
    form.append("text", text);
    form.append("session_id", SESSION_ID);
    const res = await fetch(`${API_BASE}/api/projects/${projectId}/semantic_edit`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /**
   * Two-hand-stretch release: resize the project's real dimensions by
   * `factor`. Silent on purpose, like postParamUpdate — this fires once per
   * gesture, not on every frame of the stretch.
   */
  async postResize(projectId, factor) {
    try {
      const result = await this._postJson(
        `${API_BASE}/api/projects/${projectId}/resize`,
        { factor, session_id: SESSION_ID },
        30000
      );
      await this._handleResponse(result);
      return result;
    } catch (e) {
      console.error("[Percy] Resize failed:", e);
      this.onStatusMessage(`Error: ${e.message}`, false);
      return null;
    }
  }

  async _awaitJob(jobId) {
    const url = `${API_BASE}/api/jobs/${jobId}?session_id=${SESSION_ID}`;
    const deadline = Date.now() + JOB_MAX_MS;
    let misses = 0;

    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
      let data;
      try {
        data = await this._fetchJson(url, {}, 20000);
      } catch (e) {
        if (++misses > JOB_MAX_MISSES) throw e;
        continue;
      }
      misses = 0;
      if (data.action !== "building" && data.action !== "searching" && data.action !== "publishing") {
        return data;
      }
      if (data.action === "searching" || data.action === "publishing") {
        this.onJobProgress(data);
        this.onStatusMessage(data.reply || "Working…", true);
        continue;
      }
      const secs = Math.round((data.latency_ms?.elapsed_ms || 0) / 1000);
      this.onStatusMessage(`Still sculpting… ${secs}s`, true);
    }
    throw new Error("Build timed out");
  }

  // Voice/text commands that kick off a mesh build return immediately with
  // action="building" + job_id (see app/pipeline.py's _start_mesh_build) so
  // Percy can speak an instant ack instead of going silent for the sculpt's
  // full 10-90s+. Speak the ack, then poll the same way choosePhoto() does,
  // and hand the finished build to _handleResponse once it lands.
  async _deliverResponse(result) {
    if ((result?.action !== "building" && result?.action !== "searching"
         && result?.action !== "publishing" && result?.action !== "semantic_edit")
        || !result?.job_id) {
      await this._handleResponse(result);
      return result;
    }
    await this._handleResponse(result);
    try {
      const final = await this._awaitJob(result.job_id);
      await this._handleResponse(final);
      return final;
    } catch (e) {
      voiceState.toError(e.message);
      this.onStatusMessage(`Error: ${e.message}`, false);
      setTimeout(() => voiceState.toIdle(), 3000);
      return null;
    }
  }

  async _handleResponse(data) {
    const heard = data.transcript ? `"${data.transcript}" → ` : "";
    const ms = data.latency_ms?.total_ms ? ` (${Math.round(data.latency_ms.total_ms)}ms)` : "";

    if (data.action === "searching" || data.action === "publishing") {
      this.onJobProgress(data);
    }

    const isPhotoListing = data.action === "find_photos" || data.action === "browse_photos";
    if (isPhotoListing && (data.candidates || []).length) {
      this.onJobProgress(null);
      this.onItems(null);
      this.onPhotoCandidates(data.candidates);
    } else if (data.action === "show_items" && (data.items || []).length) {
      this.onJobProgress(null);
      this.onPhotoCandidates(null);
      this.onItems(data.items);
    } else if (data.action !== "searching" && data.action !== "publishing") {
      this.onJobProgress(null);
      this.onPhotoCandidates(null);
      this.onItems(null);
    }

    this.onModelUpdate(data);

    if (data.reply_audio_url) {
      voiceState.toSpeaking();
      this.onStatusMessage(`${heard}${data.reply}${ms}`, data.ok);

      try {
        this.replyAudio = new Audio(data.reply_audio_url);
        await new Promise((resolve) => {
          this.replyAudio.onended = resolve;
          this.replyAudio.onerror = resolve;
          this.replyAudio.play().catch(resolve);
        });
      } catch (_) {}

      voiceState.toIdle();
    } else {
      this.onStatusMessage(`${heard}${data.reply}${ms}`, data.ok);
      voiceState.toIdle();
    }
  }

  getState() {
    return voiceState.state;
  }

  isMuted() {
    return voiceState.isMuted;
  }
}
