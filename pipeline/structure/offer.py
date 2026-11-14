"""Whether the fold rule could find something to draw in a protein's PDB
entries, as far as the index loader can tell from UniProt and Pfam alone.

The fold's own choice (`fold_job.choose`, policy 1) reads each entry's
residues, its validation and its file, none of which is to hand when the
protein index is loaded. Its coverage test is: an entry's chain places
`pdb_policy.PRODUCT_COVERAGE` of a mature product, or
`pdb_policy.DOMAIN_COVERAGE` of an annotated domain of `pdb_policy.DOMAIN_MIN`
or more residues (`pdb_policy.rank`). This runs that test on what UniProt
says of the protein: its mature products as the resolver reads them from
UniProt's features (`resolve.regions_of`, `pdb_policy.products_of`), its
domains as the fold job reads them (UniProt's, and Pfam's where UniProt's
leave it uncovered), and the stretches UniProt maps each accepted entry's
chains to (`app.protein_index.pdb_stretches`). Those run over every residue
SIFTS maps, placed or not, so this is the lenient side of the rule's own test:
a protein it finds no entry to cover, the choice finds nothing to draw for.

On 2026-11-14, over a fixed sample of 400 offered proteins (seed 20261114),
the choice found nothing to draw for 92; this names 34 of them, and none of
the 300 it drew. The rest fail on what only the entries' files and validation
say, and keep AlphaFold DB's model.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Sequence

from app.protein_index import pdb_stretches
from pipeline.resolver.resolve import _KEPT, _REMOVED, features_of, regions_of
from pipeline.structure import pdb_policy, pdb_search

# Pfam's domains on one protein, as the fold job reads them.
PfamOf = Callable[[str], Sequence[pdb_search.Domain]]


def products_of(entry: dict) -> tuple:
    """The protein's mature products, as the resolver and the fold job make
    them from its UniProt entry."""
    length = len((entry.get("sequence") or {}).get("value") or "")
    try:
        name = entry["proteinDescription"]["recommendedName"]["fullName"]["value"]
    except (KeyError, TypeError):
        name = entry.get("primaryAccession", "")
    features, _ = features_of(entry, length, _REMOVED + _KEPT)
    regions, _rule, cleaved, chain_label = regions_of(features, length, name)
    products, _removed = pdb_policy.products_of(regions, cleaved, length, chain_label)
    return products


def covered(entry: dict, pfam: Iterable[pdb_search.Domain] = ()) -> bool:
    """Whether one of its accepted entries' chains covers
    `PRODUCT_COVERAGE` of a mature product, or `DOMAIN_COVERAGE` of one of its
    domains (UniProt's and [pfam]) of `DOMAIN_MIN` or more residues."""
    stretches = pdb_stretches(entry)
    if not stretches:
        return False
    products = products_of(entry)
    domains = pdb_policy.domains_within(list(pdb_search.domains_of(entry)) + list(pfam),
                                        products)
    for _entry, first, last in stretches:
        for product in products:
            placed = min(last, product.end) - max(first, product.start) + 1
            if placed >= pdb_policy.PRODUCT_COVERAGE * product.length:
                return True
            for domain in domains:
                size = domain.end - domain.start + 1
                if (product.start <= domain.start and domain.end <= product.end
                        and size >= pdb_policy.DOMAIN_MIN
                        and min(last, domain.end) - max(first, domain.start) + 1
                        >= pdb_policy.DOMAIN_COVERAGE * size):
                    return True
    return False


def uncovered(entries: Sequence[dict], pfam_of: PfamOf = pdb_search.pfam_domains,
              workers: int = 4) -> tuple[set[str], list[str]]:
    """The accessions of [entries] none of whose entries covers enough to be
    drawn (`covered`), and those whose Pfam domains could not be read, which
    are left offered: a service that does not answer is never a verdict.
    Pfam is asked only about those UniProt's own domains leave uncovered,
    [workers] at a time, and once more, one at a time, about any that did not
    answer."""
    asked = [entry for entry in entries if not covered(entry)]
    found: set[str] = set()

    def judged(entry: dict) -> tuple[dict, bool | None]:
        try:
            pfam = pfam_of(entry["primaryAccession"])
        except (pdb_search.Incomplete, pdb_search.Unreachable, pdb_search.Missing):
            return entry, None
        return entry, covered(entry, pfam)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(judged, asked))
    unread: list[str] = []
    for entry, verdict in verdicts:
        if verdict is None:
            entry, verdict = judged(entry)
        if verdict is None:
            unread.append(entry["primaryAccession"])
        elif not verdict:
            found.add(entry["primaryAccession"])
    return found, sorted(unread)
