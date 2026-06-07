#!/usr/bin/env python3
"""md_to_neurips_pdf — turn a Stage-8 `stage8_paper_writer.md` into a real,
compilable NeurIPS-format PDF.

The AutoResearch Stage-8 paper_writer embeds the full paper as a fenced
```latex block (\\documentclass{article} + \\usepackage[final]{neurips_2024}).
This converter extracts that block, repairs two recurring producer defects, and
compiles it with a real LaTeX toolchain:

  1. Strips stray non-ASCII (the author block lists employee personas with CJK
     nicknames, which pdflatex's latin encoding cannot typeset).
  2. Repairs the bibliography: the producer writes references as `\\item[{...}]`
     (no citation key), so every natbib \\citep/\\citet resolves to [?]. We map
     each entry to its \\cite key by author-surname+year and rewrite it to
     `\\bibitem[{...}]{key}`.

Requires a LaTeX toolchain on PATH (latexmk + pdflatex, e.g. MacTeX/TeX Live, or
`tectonic`). neurips_2024.sty is vendored alongside this script.

Usage:
    python3 md_to_neurips_pdf.py <stage8_paper_writer.md> [-o OUTDIR]
Exit code 0 + prints the PDF path on success; non-zero on any failure.
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "neurips_assets"


def extract_latex(md_text: str):
    """Return the embedded full LaTeX document if Stage 8 emitted one as a
    ```latex fenced block, else None (caller falls back to markdown->latex)."""
    m = re.search(r"```(?:latex|tex)\s*\n(.*?)\n```", md_text, re.S)
    if not m:
        return None
    tex = m.group(1).strip() + "\n"
    if "\\documentclass" not in tex or "\\end{document}" not in tex:
        return None
    return tex


def _pandoc(md: str) -> str:
    """Convert a markdown fragment to a LaTeX fragment via pandoc.

    LLM papers write loose inline math ($ x $, $\\sim$7B, $a<b$) that violates
    pandoc's strict $...$ delimiter rules and mangles into invalid LaTeX. We
    therefore PROTECT every $$...$$ / $...$ span with an opaque placeholder
    before pandoc and restore it verbatim afterwards, so the math passes
    straight through to LaTeX (which is far more lenient than pandoc's parser).
    """
    import shutil
    import subprocess
    if not shutil.which("pandoc"):
        raise SystemExit("ERROR: paper is plain markdown (no ```latex block) and "
                         "pandoc is not on PATH — install pandoc to render it.")
    spans = []

    def _stash(m):
        spans.append(m.group(0))
        return f" ZmathZ{len(spans) - 1}ZendZ "

    protected = re.sub(r"\$\$.+?\$\$", _stash, md, flags=re.S)
    protected = re.sub(r"(?<!\\)\$[^\n]+?(?<!\\)\$", _stash, protected)

    p = subprocess.run(
        # --no-highlight: emit code blocks as plain verbatim, NOT pandoc's
        # \begin{Shaded}/\Highlighting* macros (those are only defined in
        # --standalone mode and break a fragment compile under tectonic).
        ["pandoc", "--from=markdown+pipe_tables-tex_math_dollars", "--to=latex",
         "--wrap=preserve", "--no-highlight"],
        input=protected, capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise SystemExit(f"ERROR: pandoc failed: {p.stderr[:300]}")
    out = p.stdout
    out = re.sub(r"ZmathZ(\d+)ZendZ", lambda m: spans[int(m.group(1))], out)
    return out.strip()


def markdown_to_latex(md_text: str) -> str:
    """Build a complete NeurIPS LaTeX document from a plain-markdown paper
    (the common Stage-8 output: `# Title`, `**Abstract.** ...`, `## Sections`,
    `[Author, Year]` text citations). Title/abstract are lifted out and the
    body is pandoc-converted, then wrapped in the NeurIPS preamble."""
    md = md_text.replace("\r\n", "\n")
    # (Inline/display math is protected from pandoc inside _pandoc, so loose
    # LLM math like `$\sim$7B` or `$ a<b $` passes straight through to LaTeX.)
    tm = re.search(r"^#\s+(.+?)\s*$", md, re.M)
    title_md = tm.group(1).strip() if tm else "AutoResearch Paper"
    if tm:
        md = md[:tm.start()] + md[tm.end():]
    # Abstract = a "**Abstract.** ..." lead paragraph or a "## Abstract" section.
    abstract_md = ""
    am = re.search(r"\*\*Abstract\.?\*\*\s*(.+?)(?:\n\s*\n|\n-{3,}\s*\n)", md, re.S | re.I)
    if not am:
        am = re.search(r"^#{1,3}\s*Abstract\s*\n+(.+?)(?:\n#{1,3}\s|\n-{3,}\s*\n)", md, re.S | re.I | re.M)
    if am:
        abstract_md = am.group(1).strip()
        md = md[:am.start()] + md[am.end():]
    # Drop leftover horizontal rules.
    md = re.sub(r"^\s*-{3,}\s*$", "", md, flags=re.M)

    title_tex = _pandoc(title_md).replace("\n", " ").strip() or title_md
    abstract_tex = _pandoc(abstract_md) if abstract_md else ""
    body_tex = _pandoc(md.strip())

    doc = [
        r"\documentclass{article}",
        r"\usepackage[final]{neurips_2024}",
        r"\usepackage{amsmath}", r"\usepackage{amssymb}",
        r"\usepackage{booktabs}", r"\usepackage{graphicx}",
        r"\usepackage{array}", r"\usepackage{longtable}",
        r"\usepackage{multirow}", r"\usepackage{calc}",
        r"\usepackage{hyperref}", r"\usepackage{url}",
        # pandoc emits these macros in fragment output but only DEFINES them in
        # --standalone mode; tectonic (unlike pdflatex) hard-fails on the missing
        # defs, so provide them. \real{x} just returns x for calc column-width math.
        r"\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}",
        r"\providecommand{\real}[1]{#1}",
        r"\providecommand{\passthrough}[1]{#1}",
        r"\providecommand{\pandocbounded}[1]{#1}",
        r"\title{" + title_tex + "}",
        r"\author{AutoResearch Pipeline \\ OneManCompany}",
        r"\begin{document}", r"\maketitle",
    ]
    if abstract_tex:
        doc += [r"\begin{abstract}", abstract_tex, r"\end{abstract}"]
    doc += [body_tex, r"\end{document}", ""]
    return "\n".join(doc)


def strip_non_ascii(tex: str):
    bad = sorted({c for c in tex if ord(c) >= 128})
    if not bad:
        return tex, []
    # Drop parentheticals that are wholly/partly CJK first (keeps "(EA)" etc.),
    # then remove any residual non-ASCII so latin pdflatex never chokes.
    tex = re.sub(r"[ \t]*\([^)]*[^\x00-\x7F][^)]*\)", "", tex)
    tex = "".join(ch for ch in tex if ord(ch) < 128 or ch in "\n\t")
    return tex, bad


def repair_bibitems(tex: str):
    """Rewrite key-less `\\item[{Author(Year)...}]` bibliography entries into
    natbib `\\bibitem[{...}]{key}`, matching each to a real \\cite key."""
    cite_keys = set()
    for grp in re.findall(r"\\cite[a-z]*\{([^}]*)\}", tex):
        cite_keys.update(k.strip() for k in grp.split(",") if k.strip())
    if not cite_keys:
        return tex, []

    def match_key(label: str, used: set):
        # label like "Cobbe et al.(2021)Cobbe, Kosaraj, ..." or "Qwen Math Team(2024)"
        ym = re.search(r"\((\d{4})[a-z]?\)", label)
        year = ym.group(1) if ym else ""
        head = label[: ym.start()] if ym else label
        words = re.findall(r"[A-Za-z]+", head)
        surnames = [w.lower() for w in words if w.lower() not in
                    ("et", "al", "and", "team", "the")]
        best = None
        for key in sorted(cite_keys):
            if key in used:
                continue
            kl = key.lower()
            if year and year not in kl:
                continue
            matched = [s for s in surnames if s in kl]
            if matched:
                # rank by #surnames matched (specificity), then shorter key on tie
                score = (len(matched), -len(kl))
                if best is None or score > best[0]:
                    best = (score, key)
        return best[1] if best else None

    out, converted, inbib = [], [], False
    for ln in tex.splitlines():
        if "\\begin{thebibliography}" in ln:
            inbib = True
        if "\\end{thebibliography}" in ln:
            inbib = False
        if inbib and ln.lstrip().startswith("\\item["):
            lm = re.search(r"\\item\[\{?(.*?)\}?\]", ln)
            key = match_key(lm.group(1), set(converted)) if lm else None
            if key and key not in converted:
                ln = ln.replace("\\item[", "\\bibitem[", 1)
                ln = re.sub(r"\}\]", "}]{%s}" % key, ln, count=1)
                converted.append(key)
        out.append(ln)
    missing = sorted(cite_keys - set(converted))
    return "\n".join(out) + "\n", missing


def compile_pdf(outdir: Path) -> Path:
    last = None
    if shutil.which("latexmk"):
        subprocess.run(["latexmk", "-C", "main.tex"], cwd=outdir, capture_output=True)
        last = subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode",
                               "main.tex"], cwd=outdir, capture_output=True, text=True)
    elif shutil.which("pdflatex"):
        for _ in range(3):
            last = subprocess.run(["pdflatex", "-interaction=nonstopmode", "main.tex"],
                                  cwd=outdir, capture_output=True, text=True)
    elif shutil.which("tectonic"):
        # tectonic (xetex) halts on recoverable errors; --keep-logs leaves main.log.
        last = subprocess.run(["tectonic", "--keep-logs", "main.tex"],
                              cwd=outdir, capture_output=True, text=True)
    else:
        raise SystemExit("ERROR: no LaTeX toolchain (latexmk/pdflatex/tectonic) on PATH")
    pdf = outdir / "main.pdf"
    if not pdf.exists() or pdf.stat().st_size < 1000:
        log = (outdir / "main.log")
        tail = log.read_text(errors="ignore")[-1200:] if log.exists() else ""
        err = (last.stderr or last.stdout or "")[-1200:] if last else ""
        raise SystemExit("ERROR: compilation produced no PDF.\n"
                         f"--- engine output tail ---\n{err}\n--- main.log tail ---\n{tail}")
    # report unresolved citations from the final log
    undef = 0
    log = outdir / "main.log"
    if log.exists():
        undef = len(re.findall(r"Citation .*undefined", log.read_text(errors="ignore")))
    print(f"  undefined citations after compile: {undef}")
    return pdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md", help="path to stage8_paper_writer.md")
    ap.add_argument("-o", "--outdir", default=None, help="build dir (default: <md>_pdfbuild)")
    args = ap.parse_args()

    md_path = Path(args.md).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else \
        md_path.with_name(md_path.stem + "_pdfbuild")
    outdir.mkdir(parents=True, exist_ok=True)

    md_text = md_path.read_text(encoding="utf-8")
    tex = extract_latex(md_text)
    if tex is not None:
        print("  source: embedded ```latex block")
        tex, stripped = strip_non_ascii(tex)
        if stripped:
            print(f"  stripped {len(stripped)} non-ASCII char class(es): {''.join(stripped)[:40]}")
        tex, missing = repair_bibitems(tex)
        if missing:
            print(f"  WARNING: no bibliography entry matched cite keys: {missing}")
    else:
        print("  source: plain markdown → NeurIPS LaTeX via pandoc")
        tex = markdown_to_latex(md_text)
        tex, stripped = strip_non_ascii(tex)
        if stripped:
            print(f"  stripped {len(stripped)} non-ASCII char class(es): {''.join(stripped)[:40]}")

    # Make referenced figures available next to main.tex; neutralize any that
    # are missing so a stray \includegraphics never kills the whole compile.
    md_dir = md_path.parent
    for img in set(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)):
        name = Path(img).name
        for cand in (md_dir / img, md_dir / name):
            if cand.exists() and cand.is_file():
                try:
                    shutil.copy(cand, outdir / name)
                except Exception:
                    pass
                break

    def _keep_or_drop(m):
        name = Path(m.group(1)).name
        return m.group(0) if (outdir / name).exists() else r"\fbox{\textit{[figure unavailable]}}"
    tex = re.sub(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", _keep_or_drop, tex)

    (outdir / "main.tex").write_text(tex, encoding="utf-8")
    sty = ASSETS / "neurips_2024.sty"
    if sty.exists():
        shutil.copy(sty, outdir / "neurips_2024.sty")
    elif "neurips_2024" in tex and not (outdir / "neurips_2024.sty").exists():
        print("  WARNING: neurips_2024.sty not vendored and not in outdir")

    pdf = compile_pdf(outdir)
    print(f"OK: {pdf}  ({pdf.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
