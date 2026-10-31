"""Keep chart references accurate without guessing the final visual position."""

import re

VISUAL = (
    r"(?:(?:line|bar|scatter|pie|Mermaid)[ \t]{1,8})?"
    r"(?:chart|graph|diagram|plot|figure|visualization)s?"
)
REFERENCE = re.compile(
    rf"\b(?:"
    rf"(?P<lead>(?:the|this|that|these|those)[ \t]{{1,8}})"
    rf"(?:above|below)[ \t]{{1,8}}(?P<before>{VISUAL})|"
    rf"(?P<after>{VISUAL})[ \t]{{1,8}}"
    rf"(?:(?:shown|displayed|presented|rendered)[ \t]{{1,8}})?(?:above|below)"
    rf"(?=$|[.,;:!?)]|[ \t]{{1,8}}(?:shows?|is|are|was|were|has|have|can|will|"
    rf"illustrates?|compares?|presents?|displays?|highlights?|plots?|summarizes?|"
    rf"depicts?|indicates?|demonstrates?|provides?|contains?|uses?|helps?|reveals?)\b)"
    rf")\b",
    re.I,
)
CODE_DELIMITER = re.compile(r"(`+|~{3,})")


class VisualWording:
    def __init__(self, emit):
        self.emit = emit
        self.buffer = ""
        self.code = ""

    def _write(self, text, following=""):
        # Track code delimiters across chunks; quoted code is never rewritten.
        parts = CODE_DELIMITER.split(text)
        for index, part in enumerate(parts):
            if index % 2:
                if not self.code:
                    self.code = part
                elif part == self.code:
                    self.code = ""
                self.emit(part)
            elif part:
                if self.code:
                    self.emit(part)
                    continue
                # The final segment still needs the unconsumed right context: ending
                # this emitted chunk at "below" must not turn "below 20" into a claim.
                context = part + (following if index == len(parts) - 1 else "")
                consumed = 0
                for match in REFERENCE.finditer(context):
                    if match.end() > len(part):
                        break
                    self.emit(part[consumed : match.start()])
                    self.emit((match["lead"] or "") + (match["before"] or match["after"]))
                    consumed = match.end()
                self.emit(part[consumed:])

    def feed(self, text):
        self.buffer += text
        # Bounded look-behind lets a phrase span provider chunks without flashing
        # "chart above" before it can be corrected. No whole-response buffering.
        cut = len(self.buffer) - 80
        if cut <= 0:
            return
        boundary = self.buffer.rfind(" ", 0, cut)
        if boundary >= 0:
            cut = boundary
        for match in REFERENCE.finditer(self.buffer):
            if match.start() < cut < match.end():
                cut = match.start()
        # Do not split Markdown backtick/fence delimiters.
        while cut > 0 and self.buffer[cut - 1] in "`~":
            cut -= 1
        if cut:
            self._write(self.buffer[:cut], self.buffer[cut:])
            self.buffer = self.buffer[cut:]

    def finish(self):
        self._write(self.buffer)
        self.buffer = ""
        self.code = ""


def neutral_visual_text(text):
    parts = []
    wording = VisualWording(parts.append)
    wording.feed(text)
    wording.finish()
    return "".join(parts)
