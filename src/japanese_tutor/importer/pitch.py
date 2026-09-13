"""Parse observed notation using human-confirmed conventions for this textbook."""

import re

from japanese_tutor.schemas.source import LexicalNotation, PitchAccent, UtteranceProsody

NUMBERS = {"⓪": 0, **{chr(0x2460 + i - 1): i for i in range(1, 21)}}
CIRCLED = "[" + "".join(NUMBERS) + "]"
PITCH_PATTERN = re.compile(CIRCLED + "(?:[＋+]?" + CIRCLED + ")*")


def parse_pitch(raw: str, *, series: str = "liangshuang", is_phrase: bool = False) -> PitchAccent:
    if not PITCH_PATTERN.fullmatch(raw):
        raise ValueError(f"Invalid accent notation: {raw}")
    numbers = [NUMBERS[c] for c in raw if c in NUMBERS]
    connection = "single"
    if len(numbers) > 1:
        connection = "plus" if "+" in raw or "＋" in raw else "adjacent"
    interpretation = "single" if connection == "single" else "unresolved"
    if series == "liangshuang":
        if connection == "plus" and all(len(p) == 1 for p in re.split(r"[＋+]", raw)):
            interpretation = "component_accents"
        elif connection == "adjacent" and not is_phrase:
            interpretation = "alternatives"
    return PitchAccent(
        raw_notation=raw,
        accent_numbers=numbers,
        connection=connection,
        status="observed" if interpretation != "unresolved" else "unresolved",
        interpretation=interpretation,
    )


def lexical_notations(text: str, *, series: str = "liangshuang") -> list[LexicalNotation]:
    result = []
    # A cell may have indented component words and a wrapped accent marker.
    lines = text.splitlines()
    joined = []
    for line in lines:
        if PITCH_PATTERN.fullmatch(line.strip()) and joined:
            joined[-1] += line.strip()
        else:
            joined.append(line)
    for line in joined:
        match = PITCH_PATTERN.search(line)
        raw_word = line[: match.start()].strip() if match else line.strip()
        if not raw_word:
            continue
        reading_match = re.search(r"[（(]([^）)]+)[）)]", raw_word)
        reading = reading_match[1] if reading_match else None
        surface = re.sub(r"[（(][^）)]+[）)]", "", raw_word).strip()
        variants = re.findall(r"【([^】]+)】", surface)
        surface = re.sub(r"【[^】]+】", "", surface)
        status = "unknown"
        scope = "unknown"
        reading_range = None
        original = None
        if reading:
            scope = "partial" if raw_word[reading_match.end() :].strip() else "whole_word"
            status = (
                "observed"
                if (scope == "whole_word" and re.fullmatch(r"[ぁ-ゖァ-ヺー\s]+", reading))
                else "unresolved"
            )
            if series == "liangshuang":
                if re.search(r"[ァ-ヺー]", surface):
                    foreign = re.fullmatch(
                        r"([A-Za-z][A-Za-z0-9 .'-]*?)(?:\s+([ぁ-ゖァ-ヺー]+))?", reading
                    )
                    if foreign:
                        original, reading = foreign[1], foreign[2]
                        status, scope = "unknown", "unknown"
                if reading:
                    if reading.startswith(("-", "—", "－")):
                        reading = reading[1:]
                        scope = "partial"
                    if original:
                        scope = "partial"
                    if scope == "partial" and re.fullmatch(r"[ぁ-ゖァ-ヺー]+", reading):
                        prefix = (
                            surface
                            if original or raw_word[reading_match.start() + 1] in "-—－"
                            else raw_word[: reading_match.start()]
                        )
                        base = re.search(r"[\u3400-\u9fff々]+$", prefix)
                        if base:
                            reading_range = base.span()
                            status = "observed"
                    elif scope == "whole_word" and re.fullmatch(r"[ぁ-ゖァ-ヺー\s]+", reading):
                        status = "observed"
                        reading_range = (0, len(surface))
        is_phrase = bool(re.search(r"\s", surface) or (reading and re.search(r"\s", reading)))
        result.append(
            LexicalNotation(
                surface=surface,
                reading=reading,
                reading_status=status,
                reading_scope=scope,
                reading_range=reading_range,
                loanword_original=original,
                orthographic_variants=variants,
                pitch_accent=parse_pitch(match[0], series=series, is_phrase=is_phrase)
                if match
                else None,
            )
        )
    return result


def prosody_notations(text: str) -> list[UtteranceProsody]:
    return [
        UtteranceProsody(raw_notation=c, contour=contour)
        for c in text
        for symbol, contour in [("↗", "rising"), ("↘", "falling")]
        if c == symbol
    ]
