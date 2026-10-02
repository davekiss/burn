from burn import glossary as gl
from burn.cli import main
from burn.lint import fix_command, lint
from burn.render import resolve_style, timeline
from burn.words import load, replace_phrase, replace_span, save


def W(*specs):
    """W(("Hello", 0.0, 0.4), ("world.", 0.5, 0.9, 0.2)) -> word dicts (optional 4th = probability)."""
    return [dict(i=k, text=s[0], start=s[1], end=s[2], p=s[3] if len(s) > 3 else 0.99)
            for k, s in enumerate(specs)]


def texts(words):
    return " ".join(w["text"] for w in words)


# ---------------------------------------------------------------- editing
def test_replace_phrase_matches_across_case_and_keeps_trailing_punctuation():
    words = W(("within", 0, 1), ("cloud", 1, 2), ("code.", 2, 3), ("Cloud", 3, 4), ("Code", 4, 5))
    assert replace_phrase(words, "cloud code", "Claude Code") == 2
    assert texts(words) == "within Claude Code. Claude Code"


def test_replace_phrase_does_not_count_spans_already_correct():
    words = W(("Mux", 0, 1), ("mux", 1, 2))
    assert replace_phrase(words, "Mux", "Mux") == 1


def test_replace_span_same_token_count_keeps_original_timing():
    words = W(("cloud", 1.0, 1.2), ("code", 1.3, 1.9))
    replace_span(words, 0, 1, "Claude Code")
    assert [(w["start"], w["end"]) for w in words] == [(1.0, 1.2), (1.3, 1.9)]


def test_replace_span_split_divides_time_by_length_and_covers_span():
    words = W(("Muxvideo", 2.0, 3.0))
    replace_span(words, 0, 0, "Mux Video!")
    assert [w["text"] for w in words] == ["Mux", "Video!"]
    assert words[0]["start"] == 2.0 and words[-1]["end"] == 3.0
    assert words[0]["end"] < words[1]["end"]
    assert words[0]["end"] - words[0]["start"] < words[1]["end"] - words[1]["start"]


# ---------------------------------------------------------------- timeline
def test_timeline_bridges_short_gaps_and_holds_before_long_pauses():
    s = resolve_style("bold")
    words = W(("one", 0.0, 0.3), ("two", 0.5, 0.8), ("three", 3.0, 3.2))
    (s1, e1, t1), (s2, e2, _), (s3, e3, _) = timeline(words, s)
    assert e1 == 0.5                    # 0.2s gap: stays up until "two"
    assert e2 == 0.8 + s["hold"]        # long pause: lingers briefly, then clears
    assert e2 < s3
    assert t1 == "ONE"                  # bold is uppercase


def test_timeline_strips_soft_punctuation_but_keeps_questions():
    s = resolve_style("clean")
    out = timeline(W(("Well,", 0, 1), ("really?", 1, 2)), s)
    assert [t for *_, t in out] == ["Well", "really?"]


# ---------------------------------------------------------------- lint
def apply_lint_fixes(tmp_path, words, terms=()):
    path = tmp_path / "w.json"
    save(path, dict(source="x.mp4", reviewed=False, words=words))
    cmd = fix_command(str(path), lint(load(path)["words"], terms))
    assert cmd, "expected auto fixes"
    import shlex
    main(shlex.split(cmd)[1:])
    return texts(load(path)["words"])


def test_lint_auto_fixes_produce_clean_text(tmp_path):
    words = W(("so", 0, .2), ("i've", .2, .4), ("got", .4, .6), ("it.", .6, .8),
              ("okay,", 1, 1.2), ("well,", 1.2, 1.4), ("You", 1.4, 1.6), ("need", 1.6, 1.8), ("MUX.", 1.8, 2.0),
              ("Peace.", 3, 3.4), ("Thank", 4.0, 4.0, 0.06), ("you.", 4.0, 4.1))
    out = apply_lint_fixes(tmp_path, words, [gl.Term("Mux")])
    assert out == "So I've got it. Okay, well, you need Mux. Peace."


def test_lint_keeps_real_thank_you_mid_video():
    words = W(("Thank", 0, .3), ("you", .3, .5), ("all", .5, .7), ("for", .7, .9), ("coming.", .9, 1.3))
    assert not [x for x in lint(words) if x.kind == "hallucination"]


def test_lint_flags_glossary_near_miss_for_review_not_auto():
    words = W(("within", 0, 1), ("cloud", 1, 2), ("code.", 2, 3))
    hits = [x for x in lint(words, [gl.Term("Claude Code")]) if x.kind == "glossary-near-miss"]
    assert len(hits) == 1 and hits[0].confidence == "check"
    assert hits[0].fix == "--replace cloud code=>Claude Code"


def test_lint_repeat_is_a_suggestion_only():
    issues = lint(W(("I've", 0, .2), ("I've", .2, .4), ("seen", .4, .6)))
    rep = [x for x in issues if x.kind == "repeat"]
    assert rep and rep[0].confidence == "check"
    assert fix_command("w.json", issues) is None


# ---------------------------------------------------------------- glossary
def test_glossary_applies_aliases_and_casing(tmp_path):
    path = tmp_path / "g.txt"
    gl.add(["Claude Code <= cloud code", "Mux", ".env"], path)
    gl.add(["Claude Code <= clod code"], path)  # merges aliases, no duplicate term
    terms = gl.load(path)
    assert [t.text for t in terms] == ["Claude Code", "Mux", ".env"]
    assert terms[0].aliases == ["cloud code", "clod code"]
    words = W(("clod", 0, 1), ("code", 1, 2), ("and", 2, 3), ("mux", 3, 4), ("env,", 4, 5))
    gl.apply(words, terms)
    assert texts(words) == "Claude Code and Mux .env,"


def test_glossary_prompt_is_bounded():
    terms = [gl.Term(f"Term{n:04d}") for n in range(500)]
    p = gl.prompt(terms, "extra words")
    assert p.startswith("extra words, Term0000") and len(p) <= gl.PROMPT_CHARS


def test_lint_deletes_punctuation_only_words_and_ignores_cross_sentence_repeats():
    words = W(("Find", 0, .3), ("it.", .3, .6), ("It", .7, .9), ("works.", .9, 1.2), (".", 1.5, 1.7, 0.05))
    issues = lint(words)
    assert [x.kind for x in issues if x.confidence == "auto"] == ["empty"]
    assert not [x for x in issues if x.kind == "repeat"]


def test_lint_applies_glossary_aliases_added_after_transcription(tmp_path):
    words = W(("The", 0, .3), ("model", .3, .6), ("reads", .6, .9), (".m", .9, 1.1), ("files.", 1.1, 1.5))
    assert apply_lint_fixes(tmp_path, words, [gl.Term(".env", [".m"])]) == "The model reads .env files."
