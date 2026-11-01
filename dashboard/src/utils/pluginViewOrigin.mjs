const OPAQUE_SANDBOX = "allow-scripts allow-forms allow-downloads";

function exactHttpsOrigin(value) {
  if (typeof value !== "string") return "";
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && parsed.origin === value
      ? value : "";
  } catch {
    return "";
  }
}

export function resolvePluginFrame(entry, parentOrigin) {
  const contentUrl = new URL(entry.content_path, parentOrigin);
  if (contentUrl.origin !== parentOrigin) {
    throw new Error("Plugin content path must belong to the dashboard origin.");
  }
  contentUrl.searchParams.delete("parent_origin");
  const documentOrigin = exactHttpsOrigin(entry.document_origin);
  const allowedParents = Array.isArray(entry.parent_origins) ? entry.parent_origins : [];
  const isolated = documentOrigin && documentOrigin !== parentOrigin
    && allowedParents.includes(parentOrigin);
  if (isolated) {
    const isolatedUrl = new URL(contentUrl.pathname + contentUrl.search + contentUrl.hash, documentOrigin);
    isolatedUrl.searchParams.set("parent_origin", parentOrigin);
    return {
      src: isolatedUrl.href,
      expectedOrigin: documentOrigin,
      sandbox: `${OPAQUE_SANDBOX} allow-same-origin`,
    };
  }
  return {
    src: contentUrl.href,
    expectedOrigin: "null",
    sandbox: OPAQUE_SANDBOX,
  };
}

export function isPluginFrameMessage(event, frameWindow, expectedOrigin) {
  return Boolean(frameWindow) && event.source === frameWindow
    && event.origin === expectedOrigin;
}
