import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { isPluginFrameMessage, resolvePluginFrame } from "../src/utils/pluginViewOrigin.mjs";

const parentOrigin = "https://11tiger.work:6185";
const documentOrigin = "https://11tiger.work:6186";
const entry = {
  content_path: "/api/v1/plugins/demo/views/house/_t/token/?asset_token=token",
  document_origin: documentOrigin,
  parent_origins: [parentOrigin],
};

test("a configured separate origin has stable storage and an exact parent hint", () => {
  const frame = resolvePluginFrame(entry, parentOrigin);
  assert.equal(new URL(frame.src).origin, documentOrigin);
  assert.equal(new URL(frame.src).searchParams.get("parent_origin"), parentOrigin);
  assert.equal(new URL(frame.src).searchParams.get("asset_token"), "token");
  assert.equal(frame.expectedOrigin, documentOrigin);
  assert.match(frame.sandbox, /allow-same-origin/);
});

test("localhost, missing policies and same-origin documents stay opaque", () => {
  for (const [value, parent] of [
    [entry, "http://127.0.0.1:6185"],
    [{ content_path: entry.content_path }, parentOrigin],
    [{ ...entry, document_origin: parentOrigin }, parentOrigin],
    [{ ...entry, parent_origins: ["*"] }, parentOrigin],
    [{ ...entry, document_origin: documentOrigin + "/untrusted" }, parentOrigin],
    [{ ...entry, document_origin: "javascript:alert(1)" }, parentOrigin],
  ]) {
    const frame = resolvePluginFrame(value, parent);
    assert.equal(new URL(frame.src).origin, parent);
    assert.equal(frame.expectedOrigin, "null");
    assert.doesNotMatch(frame.sandbox, /allow-same-origin/);
    assert.equal(new URL(frame.src).searchParams.has("parent_origin"), false);
  }
});

test("frame message acceptance checks both exact window and selected origin", () => {
  const iframe = {};
  assert.equal(isPluginFrameMessage({ source: iframe, origin: documentOrigin }, iframe, documentOrigin), true);
  for (const origin of [parentOrigin, "null", "https://evil.example"]) {
    assert.equal(isPluginFrameMessage({ source: iframe, origin }, iframe, documentOrigin), false);
  }
  assert.equal(isPluginFrameMessage({ source: {}, origin: documentOrigin }, iframe, documentOrigin), false);
  assert.equal(isPluginFrameMessage({ source: iframe, origin: "null" }, iframe, "null"), true);
});

function sdkFixture(selfOrigin, configuredParent) {
  const sent = [];
  const listeners = [];
  const parent = { postMessage: (...args) => sent.push(args) };
  const window = { location: { origin: selfOrigin }, parent,
    addEventListener: (_kind, listener) => listeners.push(listener) };
  const document = {
    querySelector: () => configuredParent ? { content: configuredParent } : null,
    documentElement: { setAttribute() {} },
  };
  runInNewContext(readFileSync(new URL("../../astrbot/dashboard/plugin_page_bridge.js", import.meta.url), "utf8"),
    { window, document, URL, console });
  const deliver = (origin, source = parent, channel = "astrbot-plugin-page") => {
    listeners[0]({ source, origin, data: { channel, kind: "context", context: { locale: "verified" } } });
  };
  return { sent, deliver, window, parent };
}

test("isolated SDK targets the declared parent and rejects forged origin or window", () => {
  const sdk = sdkFixture(documentOrigin, parentOrigin);
  assert.equal(sdk.sent[0][1], parentOrigin);
  sdk.deliver("https://evil.example");
  sdk.deliver(documentOrigin);
  sdk.deliver(parentOrigin, {});
  assert.equal(sdk.window.AstrBotPluginView.getContext(), null);
  sdk.deliver(parentOrigin);
  assert.equal(sdk.window.AstrBotPluginView.getContext().locale, "verified");
});

test("legacy SDK stays same-origin and does not trust a malformed metadata hint", () => {
  for (const hint of [null, "*", parentOrigin + "/untrusted", "http://evil.example"]) {
    const sdk = sdkFixture(parentOrigin, hint);
    assert.equal(sdk.sent[0][1], parentOrigin);
    sdk.deliver("https://evil.example");
    assert.equal(sdk.window.AstrBotPluginView.getContext(), null);
    sdk.deliver(parentOrigin);
    assert.equal(sdk.window.AstrBotPluginView.getContext().locale, "verified");
  }
});
