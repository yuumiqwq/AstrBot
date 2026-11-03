export function removePresetPairs(dialogs, segments, pairIndexes) {
  const removed = new Set(pairIndexes);
  const nextDialogs = [];
  const nextSegments = [];
  for (const segment of segments) {
    const startPair = nextDialogs.length / 2;
    const origins = [];
    for (let offset = 0; offset < segment.pair_count; offset += 1) {
      const pair = segment.start_pair + offset;
      if (!removed.has(pair)) {
        nextDialogs.push(...dialogs.slice(pair * 2, pair * 2 + 2));
        if (segment.source_messages) origins.push(segment.source_messages[offset]);
      }
    }
    const count = nextDialogs.length / 2 - startPair;
    if (count)
      nextSegments.push({
        ...segment,
        start_pair: startPair,
        pair_count: count,
        ...(segment.source_messages ? { source_messages: origins } : {}),
      });
  }
  return { begin_dialogs: nextDialogs, segments: nextSegments };
}

export function selectedHistoryRanges(messages, selectedIndexes) {
  const selected = new Set(selectedIndexes);
  const ranges = [];
  let range = null;
  for (const message of messages) {
    if (selected.has(message.index)) {
      if (!range) {
        range = { start: message.index, end: message.index };
        ranges.push(range);
      } else range.end = message.index;
    } else range = null;
  }
  return ranges;
}

export function validExampleDialogs(dialogs) {
  return (
    dialogs.length >= 2 &&
    dialogs.length % 2 === 0 &&
    dialogs.every(
      (value, index) =>
        typeof value === "string" && (index === 0 || Boolean(value.trim())),
    )
  );
}
