"""The fold rule's coverage, as the index loader judges it from UniProt and
Pfam (`offer.py`): offline, on a made-up precursor of 200 residues, a signal
peptide (1-20) and its chain (21-200), with a domain UniProt names (50-120)
and one only Pfam does (125-190)."""

import pytest

from pipeline.structure import offer, pdb_search
from pipeline.structure.pdb_search import Domain


def _feature(kind, start, end, description=""):
    return {"type": kind, "description": description,
            "location": {"start": {"value": start, "modifier": "EXACT"},
                         "end": {"value": end, "modifier": "EXACT"}}}


def _entry(*pdb, accession="P99999"):
    return {
        "primaryAccession": accession,
        "sequence": {"value": "M" + "A" * 199, "length": 200},
        "proteinDescription": {"recommendedName": {"fullName": {"value": "Made-up protein"}}},
        "features": [_feature("Signal", 1, 20), _feature("Chain", 21, 200, "Made-up protein"),
                     _feature("Domain", 50, 120, "Kinase")],
        "uniProtKBCrossReferences": [
            {"database": "PDB", "id": entry, "properties": [
                {"key": "Method", "value": method}, {"key": "Resolution", "value": resolution},
                {"key": "Chains", "value": chains}]}
            for entry, method, resolution, chains in pdb],
    }


PFAM = (Domain("Domain", "Made-up fold", 125, 190, "Pfam PF99999"),)


def test_the_mature_chain_is_the_resolvers():
    assert [(p.start, p.end) for p in offer.products_of(_entry())] == [(21, 200)]


@pytest.mark.parametrize("pdb, expected", [
    # 140 of the chain's 180 residues: 78%.
    (("1AAA", "X-ray", "2.00 A", "A/B=21-160"), True),
    # 64 of the domain's 71: 90.1%.
    (("2BBB", "X-ray", "2.00 A", "A=55-118"), True),
    # 63 of the domain's 71: 88.7%.
    (("2BBC", "X-ray", "2.00 A", "A=56-118"), False),
    # A peptide of 31 residues, in no domain.
    (("3CCC", "NMR", "-", "A=130-160"), False),
    # The whole chain, but past the rule's resolution.
    (("4DDD", "X-ray", "3.50 A", "A=21-200"), False),
    # The whole chain, in a computed model.
    (("4DDE", "Model", "-", "A=21-200"), False),
])
def test_an_entry_covers_70_percent_of_a_chain_or_90_of_a_domain(pdb, expected):
    assert offer.covered(_entry(pdb)) is expected


def test_a_domain_only_pfam_names_counts_once_it_is_read():
    entry = _entry(("5EEE", "EM", "3.20 A", "A=126-189"))
    assert offer.covered(entry) is False
    assert offer.covered(entry, PFAM) is True


def test_a_protein_with_no_pdb_entry_is_not_covered():
    assert offer.covered(_entry()) is False


def test_pfam_is_asked_only_where_uniprot_leaves_a_protein_uncovered():
    chain = _entry(("1AAA", "X-ray", "2.00 A", "A=21-160"), accession="P00001")
    pfam_domain = _entry(("5EEE", "EM", "3.20 A", "A=126-189"), accession="P00002")
    peptide = _entry(("3CCC", "NMR", "-", "A=130-160"), accession="P00003")
    unanswered = _entry(("3CCD", "NMR", "-", "A=130-160"), accession="P00004")
    late = _entry(("5EEF", "EM", "3.20 A", "A=126-189"), accession="P00005")
    asked = []

    def pfam_of(accession):
        asked.append(accession)
        if accession == "P00004" or (accession == "P00005" and asked.count(accession) == 1):
            raise pdb_search.Unreachable("InterPro did not answer")
        return PFAM if accession in ("P00002", "P00005") else ()

    found, unread = offer.uncovered([chain, pfam_domain, peptide, unanswered, late], pfam_of)
    # Asked once more, one at a time, where it did not answer.
    assert sorted(asked) == ["P00002", "P00003", "P00004", "P00004", "P00005", "P00005"]
    assert found == {"P00003"}
    # A service that does not answer is never a verdict: it stays offered.
    assert unread == ["P00004"]
