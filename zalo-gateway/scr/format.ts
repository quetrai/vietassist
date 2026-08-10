export type ZaloTextStyle = "b" | "i";

export type ZaloStyle = {
  start: number;
  len: number;
  st: ZaloTextStyle;
};

export type FormattedZaloMessage = {
  text: string;
  styles: ZaloStyle[];
};

export const DEFAULT_ZALO_MAX_LEN = 1800;

const ZALO_BOLD: ZaloTextStyle = "b";
const ZALO_ITALIC: ZaloTextStyle = "i";

function mergeStyle(styles: ZaloStyle[], next: ZaloStyle): void {
  const previous = styles[styles.length - 1];
  if (
    previous &&
    previous.st === next.st &&
    previous.start + previous.len === next.start
  ) {
    previous.len += next.len;
    return;
  }
  styles.push(next);
}

/**
 * Convert the markdown-lite emitted by the AI providers into Zalo text plus
 * character-range styles. Zalo does not expose a markdown parser, so markers
 * are removed and style ranges are calculated against the final text.
 */
export function markdownToZalo(text: string): FormattedZaloMessage {
  const source = text.replace(/\r\n?/g, "\n");
  const output: string[] = [];
  const styles: ZaloStyle[] = [];
  let position = 0;

  const append = (value: string, style?: ZaloTextStyle): void => {
    if (!value) return;
    const start = position;
    output.push(value);
    position += value.length;
    if (style) mergeStyle(styles, {start, len: value.length, st: style});
  };

  const parseInline = (line: string): void => {
    let i = 0;
    while (i < line.length) {
      // Parse links before emphasis so underscores/asterisks inside URLs can
      // never be mistaken for markdown emphasis.
      const link = line
        .slice(i)
        .match(/^\[([^\[\]\n]+)\]\((https?:\/\/[^\s()]+)\)/u);
      if (link) {
        append(`${link[1]} (${link[2]})`);
        i += link[0].length;
        continue;
      }

      if (line[i] === "`") {
        const end = line.indexOf("`", i + 1);
        if (end > i + 1) {
          append(line.slice(i + 1, end));
          i = end + 1;
          continue;
        }
      }

      const bold = line.slice(i).match(/^\*\*(.+?)\*\*/u);
      if (bold) {
        append(bold[1], ZALO_BOLD);
        i += bold[0].length;
        continue;
      }

      const italicStar = line.slice(i).match(/^\*(\S(?:.*?\S)?)\*/u);
      if (italicStar) {
        append(italicStar[1], ZALO_ITALIC);
        i += italicStar[0].length;
        continue;
      }

      const italicUnderscore = line
        .slice(i)
        .match(/^_(?!_)(?!\s)(.+?)(?<!\s)(?<!_)_(?![\w])/u);
      if (italicUnderscore) {
        append(italicUnderscore[1], ZALO_ITALIC);
        i += italicUnderscore[0].length;
        continue;
      }

      append(line[i]);
      i += 1;
    }
  };

  const lines = source.split("\n");
  lines.forEach((line, index) => {
    const bullet = line.match(/^[ \t]*[-*][ \t]+/u);
    if (bullet) {
      append("• ");
      parseInline(line.slice(bullet[0].length));
    } else {
      parseInline(line);
    }
    if (index < lines.length - 1) append("\n");
  });

  return {text: output.join(""), styles};
}

function safeCut(text: string, cut: number): number {
  let safe = Math.max(1, Math.min(cut, text.length));
  // JS string indexes are UTF-16 code units. Never split a surrogate pair.
  if (safe < text.length) {
    const previous = text.charCodeAt(safe - 1);
    const next = text.charCodeAt(safe);
    if (previous >= 0xd800 && previous <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
      safe -= 1;
    }
  }
  return Math.max(1, safe);
}

function preferredCut(text: string, limit: number): number {
  if (text.length <= limit) return text.length;
  let cut = text.lastIndexOf("\n\n", limit);
  if (cut < limit / 2) cut = text.lastIndexOf("\n", limit);
  if (cut < limit / 2) cut = text.lastIndexOf(" ", limit);
  if (cut <= 0) cut = limit;
  return safeCut(text, cut);
}

/**
 * Split an already formatted Zalo message. This is deliberately performed
 * after markdown conversion so a chunk can never cut through **bold**,
 * _italic_, `code`, or a markdown link. Style ranges are clipped/rebased for
 * every chunk.
 */
export function splitFormattedMessage(
  message: FormattedZaloMessage,
  limit = DEFAULT_ZALO_MAX_LEN,
): FormattedZaloMessage[] {
  if (!Number.isInteger(limit) || limit < 1) {
    throw new Error("Zalo message limit must be a positive integer");
  }

  const chunks: FormattedZaloMessage[] = [];
  let offset = 0;

  while (offset < message.text.length) {
    const remaining = message.text.slice(offset);
    const localCut = preferredCut(remaining, limit);
    const end = offset + localCut;
    const chunkText = message.text.slice(offset, end).trimEnd();
    const actualEnd = offset + chunkText.length;

    if (chunkText) {
      const chunkStyles: ZaloStyle[] = [];
      for (const style of message.styles) {
        const styleStart = style.start;
        const styleEnd = style.start + style.len;
        const clippedStart = Math.max(styleStart, offset);
        const clippedEnd = Math.min(styleEnd, actualEnd);
        if (clippedStart < clippedEnd) {
          chunkStyles.push({
            start: clippedStart - offset,
            len: clippedEnd - clippedStart,
            st: style.st,
          });
        }
      }
      chunks.push({text: chunkText, styles: chunkStyles});
    }

    offset = actualEnd;
    while (offset < message.text.length && /\s/u.test(message.text[offset])) offset += 1;
  }

  return chunks;
}
