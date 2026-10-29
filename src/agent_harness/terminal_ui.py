"""Small dependency-free terminal formatting; no permission decisions."""

import json
import shutil
import sys
import textwrap
from pathlib import Path


def terminal_text(text, *, multiline=True):
    return "".join(
        character
        if character.isprintable() or (multiline and character in "\n\t")
        else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


def width():
    return max(20, min(96, shutil.get_terminal_size((98, 24)).columns - 2))


def print_block(text, *, stream=None, indent="  "):
    stream = stream if stream is not None else sys.stdout
    fenced = False
    for line in terminal_text(text).splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            print(indent + line, file=stream)
        elif fenced or line.startswith(("    ", "\t")):
            print(indent + line, file=stream)
        elif line:
            print(
                textwrap.fill(
                    line,
                    width=width(),
                    initial_indent=indent,
                    subsequent_indent=indent,
                    break_long_words=False,
                    break_on_hyphens=False,
                ),
                file=stream,
            )
        else:
            print(file=stream)


def print_reply(answer):
    print("\nAssistant")
    print_block(answer)
    print()


def print_field(label, value, *, stream):
    value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    text = terminal_text(str(label), multiline=False) + ": " + terminal_text(value, multiline=False)
    print(
        textwrap.fill(text, width=width(), initial_indent="  ", subsequent_indent="    "),
        file=stream,
    )


def print_approval(details):
    stream = sys.stderr
    print("\nApproval required", file=stream)
    print_field("Tool", details["tool"], stream=stream)
    print_field("Permission", ", ".join(details["capabilities"]), stream=stream)
    arguments = details["arguments"]
    for key, value in arguments.items():
        if key == "content" and isinstance(value, str):
            continue
        if key == "path" and isinstance(value, str):
            try:
                value = str(Path(value).relative_to(Path.cwd()))
            except ValueError:
                pass
            key = "File"
        print_field(key.capitalize(), value, stream=stream)
    content = arguments.get("content")
    if isinstance(content, str):
        print_field("Content", f"{len(content)} characters", stream=stream)
        preview = content[:600]
        if len(preview.splitlines()) > 8:
            preview = "\n".join(preview.splitlines()[:8])
        print("\n  Content preview", file=stream)
        print_block(preview, stream=stream, indent="    ")
        if preview != content:
            print("    ... preview truncated; choose v to see full content", file=stream)
