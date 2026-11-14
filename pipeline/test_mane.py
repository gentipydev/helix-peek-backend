"""MANE as the tools read it: the genomic GFF's transcripts, and the genes whose
exons alone are over the gene page, which the record builder refuses."""

import gzip

from pipeline import mane
from pipeline.targets import GENE_PAGE_BUDGET_BP

# Two transcripts as MANE's genomic GFF files them: a gene of three exons on
# the forward strand, and one of two on the reverse, one copy of it filed on a
# patch scaffold as `NM_000002.1-2`.
_GFF = "\n".join([
    "##gff-version 3",
    "chr1\tBestRefSeq\tgene\t100\t2000\t.\t+\t.\tID=gene-ONE;Name=ONE",
    "chr1\tBestRefSeq\tmRNA\t100\t2000\t.\t+\t.\tID=rna-NM_000001.3;Parent=gene-ONE",
    "chr1\tBestRefSeq\texon\t100\t300\t.\t+\t.\tID=exon-NM_000001.3-1;Parent=rna-NM_000001.3",
    "chr1\tBestRefSeq\texon\t900\t1000\t.\t+\t.\tID=exon-NM_000001.3-2;Parent=rna-NM_000001.3",
    "chr1\tBestRefSeq\texon\t1800\t2000\t.\t+\t.\tID=exon-NM_000001.3-3;Parent=rna-NM_000001.3",
    "chr1\tBestRefSeq\tCDS\t200\t300\t.\t+\t0\tID=cds-NP_1;Parent=rna-NM_000001.3",
    "chr1\tBestRefSeq\tCDS\t900\t950\t.\t+\t1\tID=cds-NP_1;Parent=rna-NM_000001.3",
    "chr2\tBestRefSeq\tmRNA\t5000\t9000\t.\t-\t.\tID=rna-NM_000002.1-2;Parent=gene-TWO",
    "chr2\tBestRefSeq\texon\t5000\t5500\t.\t-\t.\tID=exon-NM_000002.1-2-2;Parent=rna-NM_000002.1-2",
    "chr2\tBestRefSeq\texon\t8000\t9000\t.\t-\t.\tID=exon-NM_000002.1-2-1;Parent=rna-NM_000002.1-2",
    "",
])


def test_the_gff_is_read_by_transcript_from_its_bytes_or_its_path(tmp_path):
    body = gzip.compress(_GFF.encode())
    path = tmp_path / "mane.gff.gz"
    path.write_bytes(body)
    for read in (mane.read_gff(body), mane.read_gff(path), mane.read_gff(str(path))):
        assert sorted(read) == ["NM_000001.3", "NM_000002.1"]
        one, two = read["NM_000001.3"], read["NM_000002.1"]
        assert (one["start"], one["end"], one["strand"]) == (100, 2000, 1)
        assert one["exons"] == [(100, 300), (900, 1000), (1800, 2000)]
        assert one["cds"] == [(200, 300), (900, 950)]
        # A copy on a patch scaffold is read as its own accession.
        assert (two["start"], two["end"], two["strand"]) == (5000, 9000, -1)
        assert two["exons"] == [(5000, 5500), (8000, 9000)]


def test_the_gff_read_is_the_release_the_summary_named():
    asked = []

    def get(url):
        asked.append(url)
        return gzip.compress(_GFF.encode()), {}

    assert sorted(mane.current_gff(get, "v1.5")) == ["NM_000001.3", "NM_000002.1"]
    assert asked == [mane.MANE_DIR + "MANE.GRCh38.v1.5.refseq_genomic.gff.gz"]


def _transcript(*exons):
    return {"start": exons[0][0], "end": exons[-1][1], "strand": 1,
            "exons": list(exons), "cds": []}


def test_the_genes_over_the_gene_page_are_the_ones_the_builder_refuses():
    budget = GENE_PAGE_BUDGET_BP
    transcripts = {
        # Exons of 3,000 bases across 50,000: its introns are shortened.
        "NM_000010.1": _transcript((1, 1_000), (30_001, 31_000), (52_001, 53_000)),
        # Exons a base under the page, an intron between them: built, its
        # intron at the builder's floor, a little over the page.
        "NM_000011.1": _transcript((1, 12_000), (20_001, 31_999)),
        # Exons at the page: refused.
        "NM_000012.1": _transcript((1, 12_000), (20_001, 32_000)),
        # A base over: refused.
        "NM_000013.1": _transcript((1, 12_000), (20_001, 32_001)),
        # One exon as long as the page, and no intron: it fits, and is built.
        "NM_000014.1": _transcript((1, budget)),
        # One exon a base over: refused.
        "NM_000015.1": _transcript((1, budget + 1)),
    }
    assert mane.exons_over_page(transcripts, budget) == {
        "NM_000012.1": budget,
        "NM_000013.1": budget + 1,
        "NM_000015.1": budget + 1,
    }


def test_a_transcript_the_gff_gives_no_span_is_passed_over():
    assert mane.exons_over_page({"NM_000016.1": {"exons": [(1, 30_000)], "cds": []}},
                                GENE_PAGE_BUDGET_BP) == {}
