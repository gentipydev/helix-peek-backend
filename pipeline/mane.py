"""MANE, as the tools read it: the current release's summary and genomic GFF,
from NCBI.

`scripts/load_protein_index.py` reads them through `current_summary` and
`current_gff`: the summary to join every reviewed human UniProt entry to its
MANE Select transcripts, and the GFF to find the genes whose exons alone are
over the gene page (`exons_over_page`), so there is one place that knows where
the release is listed and what its files are called. `locus/bake_locus.py`
makes the same join with MANE v1.5 as its pinned snapshot holds it
(`locus/inputs.py`), never the current release, and the census reads v1.5's
GFF (`locus/survey.py --gff`) through `read_gff`.
"""

import gzip
import io
import re
from pathlib import Path
from typing import Callable, Dict, Tuple, Union

from app.protein_index import parse_mane

MANE_DIR = "https://ftp.ncbi.nlm.nih.gov/refseq/MANE/MANE_human/current/"

# The release's genomic GFF, RefSeq's accessions (`v1.5`).
GFF = "MANE.GRCh38.{}.refseq_genomic.gff.gz"

# A fetch: the body at a URL, and its response headers.
Get = Callable[[str], Tuple[bytes, Dict[str, str]]]

# A RefSeq transcript as MANE's GFF files a copy of it: `NM_000937.5-2`.
_COPY = re.compile(r"^(N[MR]_\d+\.\d+)-\d+$")


def current_summary(get: Get) -> Tuple[Dict[str, dict], str]:
    """MANE Select rows keyed by versionless ENST, and the release ("v1.5")."""
    listing, _ = get(MANE_DIR)
    names = re.findall(r"MANE\.GRCh38\.v([0-9.]+)\.summary\.txt\.gz", listing.decode())
    if not names:
        raise SystemExit("No MANE summary in {}".format(MANE_DIR))
    mane_release = "v" + names[0]
    body, _ = get(MANE_DIR + "MANE.GRCh38.{}.summary.txt.gz".format(mane_release))
    mane = parse_mane(io.StringIO(gzip.decompress(body).decode()))
    return mane, mane_release


def current_gff(get: Get, mane_release: str) -> Dict[str, dict]:
    """The transcripts of the release `current_summary` named, from its genomic
    GFF (`read_gff`), so that the two are one release."""
    body, _ = get(MANE_DIR + GFF.format(mane_release))
    return read_gff(body)


def _accession(named: str) -> str:
    """The transcript's accession, without the number of the copy."""
    found = _COPY.match(named)
    return found.group(1) if found else named


def read_gff(source: Union[bytes, str, Path]) -> Dict[str, dict]:
    """MANE's transcripts, by RefSeq accession: the mRNA's span and strand,
    and its exons and its coding stretches, as the genome numbers them.
    `source` is the gzipped GFF: its path, or its bytes."""
    found = {}  # type: Dict[str, dict]
    raw = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with gzip.open(raw, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 9 or columns[2] not in ("mRNA", "exon", "CDS"):
                continue
            attributes = dict(item.split("=", 1) for item in columns[8].split(";")
                              if "=" in item)
            named = attributes.get("ID" if columns[2] == "mRNA" else "Parent", "")
            if not named.startswith("rna-"):
                continue
            # A transcript MANE annotates on a patch scaffold is filed as a
            # copy of its accession (`rna-NM_000937.5-2`).
            entry = found.setdefault(_accession(named[4:]), {"exons": [], "cds": []})
            start, end = int(columns[3]), int(columns[4])
            if columns[2] == "mRNA":
                entry.update(start=start, end=end, strand=1 if columns[6] == "+" else -1)
            else:
                entry["exons" if columns[2] == "exon" else "cds"].append((start, end))
    return found


def exons_over_page(transcripts: Dict[str, dict], budget: int) -> Dict[str, int]:
    """The transcripts whose exons alone are over a gene page of `budget`
    bases, each with its exons' total: those the record builder refuses, by
    its own test (`build_gene_record.intron_scale`), which this runs on each
    transcript's span and exons as `read_gff` reads them."""
    from pipeline.mock import build_gene_record as builder

    over = {}  # type: Dict[str, int]
    for accession, transcript in transcripts.items():
        if "start" not in transcript:
            continue
        payload = {
            "location": {"start": transcript["start"], "end": transcript["end"],
                         "strand": transcript["strand"]},
            "transcript": {"segments": [{"start": a, "end": b}
                                        for a, b in transcript["exons"]]},
            "exons": [],
        }
        try:
            builder.intron_scale(payload, budget)
        except ValueError:
            over[accession] = sum(b - a + 1 for a, b in builder.transcript_spans(payload))
    return over
