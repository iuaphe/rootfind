"""
post_run_analysis.py
Run after run_parallel.py finishes. Read-only on your scraped data; the only files it
writes are species_summary.csv (flags per species) and post_run_summary.pdf (a formatted
report of everything below) inside the output folder.

For the PDF (section 8) install once:  pip install reportlab matplotlib
(If they are missing, sections 1-7 still run and the PDF is skipped with a message.)

Usage:
    python post_run_analysis.py                       # auto-picks the highest output_# folder
    python post_run_analysis.py output_2              # specific folder
    python post_run_analysis.py C:\\path\\to\\output_2
"""
import datetime
import glob
import io
import os
import re
import sys
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 60)

FULL_CSV = "all_families_attributes_with_counties.csv"
MATRIX_CSV = "species_county_presence_matrix.csv"
N_BASE_COLS = 13          # Family..Special Locations in the full file
RANK_MARKERS = {"var.", "subsp.", "ssp.", "f.", "forma", "subvar.", "nothosubsp.", "nothovar."}


def header(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def find_output_dir(arg):
    if arg:
        return arg
    candidates = []
    for base in {os.getcwd(), os.path.dirname(os.path.abspath(__file__))}:
        for name in os.listdir(base):
            m = re.fullmatch(r"output_(\d+)", name)
            full = os.path.join(base, name)
            if m and os.path.isdir(full):
                candidates.append((int(m.group(1)), full))
    if not candidates:
        print("No output_# folder found. Pass the folder path as an argument.")
        sys.exit(1)
    return max(candidates)[1]


# ======================================================================
# PDF REPORT (section 8)
# ======================================================================
def build_pdf(path, ctx):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    GREEN = colors.HexColor("#1f4e3d")
    ACCENT = colors.HexColor("#3b8b5a")
    LIGHT = colors.HexColor("#f1f5f2")
    GRID = colors.HexColor("#d5ded8")
    MUTED = colors.HexColor("#5b6b62")
    STATUS_COL = {"PASS": "#1b7f3b", "WARN": "#b26a00", "FAIL": "#b3261e", "INFO": "#4a5a52"}
    W = 7.0 * inch  # usable width

    base = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24,
                                textColor=colors.white, alignment=0, leading=28, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica", fontSize=9,
                              textColor=colors.HexColor("#d8e7de"), leading=13),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=14,
                             textColor=GREEN, spaceBefore=14, spaceAfter=6, leading=17),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10.5,
                             textColor=ACCENT, spaceBefore=8, spaceAfter=3, leading=13),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=9, leading=12.5,
                               textColor=colors.HexColor("#222222")),
        "note": ParagraphStyle("n", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8,
                               leading=11, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10),
        "cellb": ParagraphStyle("cb", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10,
                                textColor=colors.white),
        "kpi_v": ParagraphStyle("kv", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=18,
                                leading=21, textColor=GREEN, alignment=1),
        "kpi_l": ParagraphStyle("kl", parent=base["Normal"], fontName="Helvetica", fontSize=7.5,
                                leading=9, textColor=MUTED, alignment=1),
    }

    def P(text, style="body"):
        return Paragraph(escape(str(text)), st[style])

    def PI(text, style="cell"):          # species names in italics
        return Paragraph(f"<i>{escape(str(text))}</i>", st[style])

    def table(rows, widths, header=True, italic_cols=(), right_cols=()):
        data = []
        for r_i, row in enumerate(rows):
            out = []
            for c_i, val in enumerate(row):
                if header and r_i == 0:
                    out.append(Paragraph(escape(str(val)), st["cellb"]))
                elif c_i in italic_cols:
                    out.append(PI(val))
                elif isinstance(val, Paragraph):
                    out.append(val)
                else:
                    out.append(P(val, "cell"))
            data.append(out)
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
        ]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), GREEN)]
            style += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2, len(rows), 2)]
        t.setStyle(TableStyle(style))
        return t

    def fig_to_image(fig, width=W):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buf.seek(0)
        from reportlab.lib.utils import ImageReader
        ir = ImageReader(buf)
        iw, ih = ir.getSize()
        buf.seek(0)
        return Image(buf, width=width, height=width * ih / iw)

    def style_axes(ax):
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color("#9aa8a0")
        ax.tick_params(colors="#3a4a42", labelsize=8)
        ax.xaxis.label.set_color("#3a4a42"); ax.yaxis.label.set_color("#3a4a42")

    story = []

    # ---------------- Title banner ----------------
    banner = Table([[Paragraph("BONAP Data Aggregation Summary", st["title"])],
                    [Paragraph(f"{escape(ctx['folder'])}<br/>Generated {ctx['generated']}", st["sub"])]],
                   colWidths=[W])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREEN),
                                ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                                ("TOPPADDING", (0, 0), (0, 0), 16), ("BOTTOMPADDING", (0, 1), (0, 1), 14)]))
    story += [banner, Spacer(1, 12)]

    # ---------------- At a glance ----------------
    def kpi(value, label):
        return [Paragraph(escape(str(value)), st["kpi_v"]), Paragraph(escape(label), st["kpi_l"])]

    cards = [
        kpi(f"{ctx['n_species']:,}", "species / taxa"),
        kpi(f"{ctx['n_families']:,}", "families"),
        kpi(f"{ctx['n_genera']:,}", "genera"),
        kpi(f"{ctx['n_counties']:,}", "counties scored"),
        kpi(f"{ctx['n_zero']:,}", f"all-zero ({ctx['zero_rate']:.1%})"),
        kpi(f"{len(ctx['fails']):,}", "failure-log lines"),
    ]
    kt = Table([[c[0] for c in cards], [c[1] for c in cards]], colWidths=[W / 6] * 6)
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                            ("BOX", (0, 0), (-1, -1), 0.6, GRID), ("LINEAFTER", (0, 0), (-2, -1), 0.4, GRID),
                            ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [Paragraph("At a glance", st["h1"]), kt]

    # ---------------- 1. Integrity ----------------
    story.append(Paragraph("1. Data integrity", st["h1"]))
    rows = [["Check", "Result", "Status"]]
    for name, val, status in ctx["checks"]:
        rows.append([name, val,
                     Paragraph(f'<font color="{STATUS_COL.get(status, "#000000")}"><b>{status}</b></font>', st["cell"])])
    story.append(table(rows, [W * 0.58, W * 0.28, W * 0.14]))
    blanks = ctx["blanks"]
    blanks = blanks[blanks > 0]
    if len(blanks):
        story += [Paragraph("Blank attribute values", st["h2"]),
                  table([["Column", "Blank rows"]] + [[k, f"{int(v):,}"] for k, v in blanks.items()],
                        [W * 0.6, W * 0.4])]

    # ---------------- 2. Zero analysis ----------------
    story.append(Paragraph("2. Species with no county records (all-zero)", st["h1"]))
    story.append(P("An all-zero row means no county in your coordinate grid was colored on that species' map. "
                   "That is either a species that lives only outside your grid (for example Hawaii, Puerto Rico or "
                   "non-US range) or a species BONAP has no map for. It is not evidence of absence.", "body"))

    def zero_rows(df, label_col_name):
        rows_ = [[label_col_name, "Zero rate", "Zero species", "Total"]]
        for idx, r in df.iterrows():
            rows_.append([str(idx), f"{r['mean']:.1%}", f"{int(r['sum']):,}", f"{int(r['count']):,}"])
        return rows_

    story += [Paragraph("By Nativity", st["h2"]),
              table(zero_rows(ctx["by_nativity"], "Nativity"), [W * 0.58, W * 0.14, W * 0.16, W * 0.12]),
              Paragraph("By major plant group", st["h2"]),
              table(zero_rows(ctx["by_group"], "Major plant group"), [W * 0.58, W * 0.14, W * 0.16, W * 0.12])]

    fz = ctx["by_family_zero"]
    fam_rows = [["Family", "Zero species", "Total species", "Zero rate"]]
    for fam, r in fz.iterrows():
        fam_rows.append([fam, f"{int(r['sum']):,}", f"{int(r['count']):,}", f"{r['sum'] / r['count']:.1%}"])
    gen_rows = [["Genus", "Zero species"]] + [[g, f"{int(n):,}"] for g, n in ctx["top_zero_genera"].head(10).items()]
    two = Table([[table(fam_rows, [W * 0.22, W * 0.11, W * 0.11, W * 0.09]),
                  table(gen_rows, [W * 0.2, W * 0.12])]], colWidths=[W * 0.56, W * 0.44])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [Paragraph("Top families and genera by zero count", st["h2"]), two]

    story.append(Paragraph("Zero streaks in file order", st["h2"]))
    story.append(P(f"Longest run of consecutive all-zero rows: {ctx['longest_run']:,}. Clustered zeros usually "
                   "mean one region-specific genus; zeros scattered across unrelated families would point to a "
                   "timing problem.", "body"))
    if ctx["run_contents"] is not None and len(ctx["run_contents"]):
        story += [Spacer(1, 4),
                  table([["Family", "Genus", "Species in run"]] +
                        [[a, b, f"{int(n):,}"] for (a, b), n in ctx["run_contents"].items()],
                        [W * 0.4, W * 0.35, W * 0.25])]

    if ctx["sample"] is not None and len(ctx["sample"]):
        story.append(Paragraph("15 random zero species to spot-check on bonap.net", st["h2"]))
        srows = [["Family", "Genus", "Species", "Nativity"]]
        for _, r in ctx["sample"].iterrows():
            srows.append([r["Family"], r["Genus"], r["Species"], r["Nativity"]])
        story.append(table(srows, [W * 0.2, W * 0.16, W * 0.34, W * 0.30], italic_cols=(2,)))

    # ---------------- 3. Charts ----------------
    story.append(Paragraph("3. Charts", st["h1"]))

    # 3a range-size histogram
    nz = np.asarray(ctx["nz_counts"])
    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.hist(nz, bins=50, color="#3b8b5a", edgecolor="white", linewidth=0.4)
    ax.set_xlabel("Counties present"); ax.set_ylabel("Species")
    ax.set_title("Range size of species with at least one county", fontsize=10, color="#1f4e3d", loc="left")
    style_axes(ax)
    story += [fig_to_image(fig), Spacer(1, 6)]

    # 3b families: data vs zero
    ft = ctx["family_table"].iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    with_data = ft["species"] - ft["zero"]
    ax.barh(ft.index, with_data, color="#3b8b5a", label="Has county data")
    ax.barh(ft.index, ft["zero"], left=with_data, color="#c9a227", label="All-zero")
    ax.set_xlabel("Species")
    ax.set_title("15 largest families: species with and without county data", fontsize=10, color="#1f4e3d", loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    style_axes(ax)
    story += [fig_to_image(fig), Spacer(1, 6)]

    # 3c mean species richness by state
    rich = ctx["richness"]
    st_map = pd.Series({c: (c.rsplit(",", 1)[1].strip() if "," in c else "?") for c in rich.index})
    by_state = rich.groupby(st_map).mean().sort_values(ascending=False).head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.barh(by_state.index, by_state.values, color="#1f4e3d")
    ax.set_xlabel("Mean species recorded per county")
    ax.set_title("Species richness by state (15 highest)", fontsize=10, color="#1f4e3d", loc="left")
    style_axes(ax)
    story += [fig_to_image(fig)]

    # ---------------- 4. Coverage ----------------
    story.append(Paragraph("4. County coverage by state", st["h1"]))
    sc = ctx["state_counts"]
    items = [(s_, int(n)) for s_, n in sc.items()]
    ncol = 4
    per = -(-len(items) // ncol)
    cols_ = [items[i * per:(i + 1) * per] for i in range(ncol)]
    cov_rows = [["State", "Counties"] * ncol]
    for r_i in range(per):
        row = []
        for c in cols_:
            row += [c[r_i][0], f"{c[r_i][1]:,}"] if r_i < len(c) else ["", ""]
        cov_rows.append(row)
    story.append(table(cov_rows, [W / 8] * 8))
    if ctx["missing_states"]:
        story += [Spacer(1, 4),
                  P("Not in the coordinate file: " + ", ".join(ctx["missing_states"]) +
                    ". Species found only there will appear as all-zero.", "note")]

    # ---------------- 5. Failure logs ----------------
    story.append(Paragraph("5. Failure logs", st["h1"]))
    fails = ctx["fails"]
    story.append(P(f"{ctx['n_log_files']} log file(s), {len(fails):,} line(s). "
                   f"{ctx['n_species_err']:,} species/genus/family-level error(s); "
                   f"{ctx['n_nomap']:,} species logged as 'nomap' (no map image retrieved, recorded as all zeros).",
                   "body"))
    if len(fails):
        lvl = fails.groupby("level").size()
        story += [Spacer(1, 4), table([["Level", "Lines"]] + [[k, f"{int(v):,}"] for k, v in lvl.items()],
                                      [W * 0.3, W * 0.2])]
        show = fails.head(30)
        story += [Spacer(1, 6),
                  table([["Level", "Family", "Genus", "Species", "Error"]] +
                        [[r.level, r.Family, r.Genus, r.Species, r.error] for r in show.itertuples()],
                        [W * 0.09, W * 0.2, W * 0.15, W * 0.31, W * 0.25], italic_cols=(3,))]
        if len(fails) > 30:
            story.append(P(f"Showing the first 30 of {len(fails):,} lines.", "note"))

    # ---------------- 6. Infraspecific ----------------
    story.append(Paragraph("6. Varieties and subspecies vs. parent species", st["h1"]))
    if ctx["infra"]:
        n_inf, n_same, share, n_same_nz, n_nz = ctx["infra"]
        story.append(P(f"{n_inf:,} infraspecific taxa have a parent species in the data. "
                       f"{n_same:,} ({share:.1%}) have a county pattern identical to the parent "
                       f"({n_same_nz:,} of {n_nz:,} when all-zero rows are excluded). A high share suggests BONAP "
                       "displays the parent map for those taxa.", "body"))
    else:
        story.append(P("No infraspecific taxa with a matching parent species were found.", "body"))

    # ---------------- 7. Range summary ----------------
    story.append(Paragraph("7. Range size summary", st["h1"]))
    rd = ctx["range_desc"]
    labels = {"count": "Species with data", "mean": "Mean counties", "std": "Std. dev.", "min": "Minimum",
              "25%": "25th percentile", "50%": "Median", "75%": "75th percentile", "max": "Maximum"}
    desc_rows = [["Statistic", "Value"]] + [[labels.get(k, k), f"{v:,.1f}"] for k, v in rd.items()]
    wide_rows = [["Widest-ranging species", "Counties"]] + [[n, f"{c:,}"] for n, c in ctx["widest"]]
    rich_rows = [["Most species-rich counties", "Species"]] + [[k, f"{int(v):,}"] for k, v in ctx["richness"].head(10).items()]
    t1 = table(desc_rows, [W * 0.17, W * 0.11])
    t2 = table(wide_rows, [W * 0.25, W * 0.09], italic_cols=(0,))
    t3 = table(rich_rows, [W * 0.2, W * 0.09])
    trio = Table([[t1, t2, t3]], colWidths=[W * 0.30, W * 0.36, W * 0.34])
    trio.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [trio, Spacer(1, 4),
              P(f"Counties with zero species recorded: {ctx['n_empty_counties']:,}.", "note")]

    # ---------------- footer / build ----------------
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(GRID); canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.6 * inch, letter[0] - 0.75 * inch, 0.6 * inch)
        canvas.setFont("Helvetica", 7.5); canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.45 * inch, "BONAP scrape summary")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.85 * inch,
                            title="BONAP Scrape Summary", author="post_run_analysis.py")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    d = find_output_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Analyzing folder: {os.path.abspath(d)}")

    matrix_path = os.path.join(d, MATRIX_CSV)
    full_path = os.path.join(d, FULL_CSV)
    for pth in (matrix_path, full_path):
        if not os.path.exists(pth):
            print(f"Missing {pth}. Has the run finished and merged?")
            sys.exit(1)

    # Read the matrix compactly (0/1 columns as uint8 instead of int64)
    cols = pd.read_csv(matrix_path, nrows=0).columns.tolist()
    dtypes = {c: "uint8" for c in cols[3:]}
    m = pd.read_csv(matrix_path, dtype=dtypes)
    f = pd.read_csv(full_path, usecols=range(N_BASE_COLS), low_memory=False)

    county_cols = list(m.columns[3:])
    arr = m[county_cols].to_numpy()
    counts = arr.sum(axis=1)
    zero = counts == 0
    ctx = {"folder": os.path.abspath(d), "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "checks": [], "notes": []}

    # ------------------------------------------------------------------
    header("1. INTEGRITY CHECKS")
    print("Matrix rows:", len(m), "| Full rows:", len(f), "| Match:", len(m) == len(f))
    ctx["checks"].append(("Matrix and full file row counts match", f"{len(m):,} vs {len(f):,}",
                          "PASS" if len(m) == len(f) else "FAIL"))
    if len(m) == len(f):
        same_keys = (m[["Family", "Genus", "Species"]].values == f[["Family", "Genus", "Species"]].values).all()
        print("Matrix and full file list the same species in the same order:", bool(same_keys))
        ctx["checks"].append(("Both files list the same species in the same order", "yes" if same_keys else "no",
                              "PASS" if same_keys else "FAIL"))
    n_dup = int(m.duplicated(["Family", "Genus", "Species"]).sum())
    print("Duplicate species:", n_dup)
    ctx["checks"].append(("Duplicate species rows", f"{n_dup:,}", "PASS" if n_dup == 0 else "WARN"))
    print("Counties (columns):", len(county_cols))
    ctx["checks"].append(("County columns in matrix", f"{len(county_cols):,}", "INFO"))
    print("All-zero species:", int(zero.sum()), f"({zero.mean():.1%})")
    ctx["checks"].append(("All-zero species (no county in grid)", f"{int(zero.sum()):,} ({zero.mean():.1%})", "INFO"))
    n_fam = int(m.Family.nunique())
    n_gen = int(m.groupby("Family").Genus.nunique().sum())
    print("Families:", n_fam, "| Genera:", n_gen)
    blanks = f.iloc[:, 3:].isna().sum()
    print("\nBlank values per attribute column:")
    print(blanks.to_string())
    ctx.update(n_species=len(m), n_families=n_fam, n_genera=n_gen, n_counties=len(county_cols),
               n_zero=int(zero.sum()), zero_rate=float(zero.mean()), blanks=blanks)

    # ------------------------------------------------------------------
    header("2. ZERO-COUNTY ANALYSIS")
    f = f.copy()
    f["zero"] = zero if len(f) == len(m) else False

    by_nativity = f.groupby("Nativity")["zero"].agg(["mean", "sum", "count"]).sort_values("sum", ascending=False).head(10)
    by_group = f.groupby("Major Plant Group")["zero"].agg(["mean", "sum", "count"]).sort_values("sum", ascending=False).head(10)
    by_family_zero = f.groupby("Family")["zero"].agg(["sum", "count"]).sort_values("sum", ascending=False).head(10)
    top_zero_genera = m[zero].groupby("Genus").size().sort_values(ascending=False).head(15)
    print("Zero rate by Nativity:\n", by_nativity)
    print("\nZero rate by Major Plant Group:\n", by_group)
    print("\nTop families by zero count:\n", by_family_zero)
    print("\nTop genera by zero count:\n", top_zero_genera.to_string())
    ctx.update(by_nativity=by_nativity, by_group=by_group, by_family_zero=by_family_zero,
               top_zero_genera=top_zero_genera)

    # Long streaks of consecutive zero rows (clustered zeros = real region, scattered = timing bug)
    z = pd.Series(zero.astype(int))
    grp = (z != z.shift()).cumsum()
    run = z.groupby(grp).transform("size") * z
    longest = int(run.max())
    print("\nLongest run of consecutive zero rows in file order:", longest)
    ctx["longest_run"] = longest
    ctx["run_contents"] = None
    if longest > 0:
        in_run = (grp == grp[run.idxmax()]).to_numpy()
        print("What is in that run (Family / Genus: species count):")
        run_contents = m[in_run].groupby(["Family", "Genus"]).size()
        print(run_contents.to_string())
        ctx["run_contents"] = run_contents.sort_values(ascending=False).head(8)

    print("\n15 random zero species to spot-check by hand on bonap.net:")
    ctx["sample"] = None
    if zero.any():
        sample = f[f.zero].sample(min(15, int(zero.sum())), random_state=1)
        print(sample[["Family", "Genus", "Species", "Nativity"]].to_string(index=False))
        ctx["sample"] = sample[["Family", "Genus", "Species", "Nativity"]]

    # ------------------------------------------------------------------
    header("3. COUNTY COVERAGE BY STATE")
    states = pd.Series([c.rsplit(",", 1)[1].strip() if "," in c else "?" for c in county_cols])
    state_counts = states.value_counts().sort_index()
    print(state_counts.to_string())
    ctx["state_counts"] = state_counts
    ctx["missing_states"] = []
    for st in ("HI", "AK", "PR", "VI"):
        if st not in set(states):
            print(f"NOTE: no '{st}' counties in your coordinate file, so species living only there will be all-zero.")
            ctx["missing_states"].append(st)

    # ------------------------------------------------------------------
    header("4. FAILURE LOGS")
    fail_files = sorted(glob.glob(os.path.join(d, "failures_worker_*.txt")))
    fail_rows = []
    for ff in fail_files:
        with open(ff, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 5:
                    fail_rows.append(parts[:5])
    fails = pd.DataFrame(fail_rows, columns=["level", "Family", "Genus", "Species", "error"])
    print(f"Log files found: {len(fail_files)} | total lines: {len(fails)}")
    if len(fails):
        print(fails.groupby("level").size().to_string())
        print(fails.to_string(index=False))
    nomap_keys = set(map(tuple, fails.loc[fails.level == "nomap", ["Family", "Genus", "Species"]].values))
    species_err = fails[fails.level.isin(["species", "genus", "family"])]
    if len(species_err):
        print("\nWARNING: species/genus/family-level errors exist. Rerun with RESUME_FOLDER to fill them in.")
    ctx.update(fails=fails, n_log_files=len(fail_files), n_species_err=len(species_err))
    ctx["checks"].append(("Species/genus/family-level scrape errors", f"{len(species_err):,}",
                          "PASS" if len(species_err) == 0 else "WARN"))

    # ------------------------------------------------------------------
    header("5. VARIETIES/SUBSPECIES IDENTICAL TO THEIR PARENT SPECIES")
    names = m["Species"].astype(str).tolist()
    index_by_name = {}
    for i, nm in enumerate(names):
        index_by_name.setdefault(nm, i)

    identical = np.zeros(len(m), dtype=bool)
    has_parent = np.zeros(len(m), dtype=bool)
    for i, nm in enumerate(names):
        tok = nm.split()
        if len(tok) > 2 and any(t in RANK_MARKERS for t in tok[2:]):
            parent = " ".join(tok[:2])
            p = index_by_name.get(parent)
            if p is not None and p != i:
                has_parent[i] = True
                identical[i] = bool((arr[i] == arr[p]).all())
    n_inf = int(has_parent.sum())
    ctx["infra"] = None
    if n_inf:
        nz = has_parent & ~zero
        print(f"Infraspecific taxa with a parent species in the data: {n_inf}")
        print(f"  identical to parent (all counties): {int(identical[has_parent].sum())} "
              f"({identical[has_parent].mean():.1%})")
        print(f"  identical to parent, excluding all-zero rows: {int((identical & nz).sum())} of {int(nz.sum())}")
        print("If this share is high, BONAP is likely showing the parent species map for those taxa.")
        ctx["infra"] = (n_inf, int(identical[has_parent].sum()), float(identical[has_parent].mean()),
                        int((identical & nz).sum()), int(nz.sum()))
    else:
        print("No infraspecific taxa with a matching parent species found.")

    # ------------------------------------------------------------------
    header("6. RANGE SIZE SUMMARY (non-zero species only)")
    nzc = counts[~zero]
    range_desc = pd.Series(nzc).describe().round(1)
    print(range_desc.to_string())
    top_idx = np.argsort(counts)[::-1][:10]
    print("\nTen widest-ranging species:")
    for i in top_idx:
        print(f"  {m.Species.iloc[i]:<45} {counts[i]:>5} counties")
    richness = pd.Series(arr.sum(axis=0), index=county_cols).sort_values(ascending=False)
    print("\nTen most species-rich counties:")
    print(richness.head(10).to_string())
    print("\nCounties with zero species recorded:", int((richness == 0).sum()))
    ctx.update(range_desc=range_desc, nz_counts=nzc,
               widest=[(m.Species.iloc[i], int(counts[i])) for i in top_idx],
               richness=richness, richness_states=states.values, n_empty_counties=int((richness == 0).sum()))

    # ------------------------------------------------------------------
    header("7. WRITING species_summary.csv")
    summary = m[["Family", "Genus", "Species"]].copy()
    summary["counties_present"] = counts
    summary["all_zero"] = zero
    summary["logged_nomap"] = [(a, b, c) in nomap_keys for a, b, c in
                               zip(summary.Family, summary.Genus, summary.Species)]
    summary["no_county_data"] = summary["all_zero"]  # zero in the 3,031-county grid (real absence OR outside coverage)
    summary["identical_to_parent"] = identical
    out_path = os.path.join(d, "species_summary.csv")
    summary.to_csv(out_path, index=False)
    print(f"Saved: {out_path}")
    print("Use 'all_zero' / 'logged_nomap' to separate species outside your county grid from real absences.")

    # ------------------------------------------------------------------
    header("8. WRITING post_run_summary.pdf")
    ctx["family_table"] = (summary.assign(has_data=~summary.all_zero)
                           .groupby("Family").agg(species=("Species", "size"), zero=("all_zero", "sum"))
                           .sort_values("species", ascending=False).head(15))
    ctx["n_nomap"] = int(summary["logged_nomap"].sum())
    pdf_path = os.path.join(d, "post_run_summary.pdf")
    try:
        build_pdf(pdf_path, ctx)
        print(f"Saved: {pdf_path}")
    except ImportError as e:
        print(f"PDF skipped ({e}). Install with:  pip install reportlab matplotlib")


if __name__ == "__main__":
    main()
