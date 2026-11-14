# The locus inputs: what they hold, and on what terms

`locus-inputs.bb475d41dcf9.jsonl.gz` is the one snapshot every protein's
`locus` track is baked from, the zoom's way in. `../inputs.py` pins the
sha256 of its text (`INPUTS_SHA256`, `bb475d41…`) and refuses any other
file. `../snapshot.py` built it on 2026-11-13 from the sources below, each
pinned to a release that no longer changes, and its header line names each
download's URL, size and sha256.

It is kept here, beside the digest that pins it, so that any checkout can
bake a locus, run the census and deploy the resolver (the decision of
2026-11-14). Until then it was git-ignored, on one Mac and in Modal's image.
A rebuild from fresh downloads was byte-identical on 2026-11-13 and on
2026-11-14, but UCSC's tables carry no version, so once one changes no
rebuild will give this file again.

## Sources and terms

Each was checked on its own site on 2026-11-14.

| Source | What the file keeps of it | Terms | Credit |
|---|---|---|---|
| The Human Protein Atlas, version 25.1, from its archive site `v25.proteinatlas.org` | Each gene's names, RNA tissue and single cell type readings and subcellular locations (`snapshot.ATLAS_FIELDS`), its consensus RNA levels by tissue and its single cell type levels. None of the third-party sets the Atlas's licence page names. | Creative Commons Attribution 4.0 International for the Atlas's copyrightable parts; cite the Atlas and the data used (`proteinatlas.org/about/licence`). | Human Protein Atlas, proteinatlas.org, version 25.1. |
| The UCSC Genome Browser's hg38 tables `cytoBand`, `chromAlias`, `altLocations` and `fixLocations`, from its REST API | Each table as the API serves it. | Freely available for any use, crediting the contributor; the data is meant for research (`genome.ucsc.edu/conditions.html`). | UCSC Genome Browser, genome.ucsc.edu; the Genome Reference Consortium's GRCh38. |
| MANE v1.5, NCBI and EMBL-EBI | Its summary's MANE Select rows. | NCBI places no restriction on the use or distribution of its molecular data (`ncbi.nlm.nih.gov/home/about/policies`). | MANE, NCBI and EMBL-EBI. |
| HGNC's complete set, its monthly archive of 2026-10-06 | Each HGNC id's Ensembl gene. | CC0; credit recommended (`genenames.org/about/license`). | HGNC, RRID:SCR_002827. |
| NCBI's placements of GRCh38.p14's alternate locus and patch scaffolds | Where each scaffold lies on its chromosome. | As MANE's: NCBI places no restriction. | NCBI; the Genome Reference Consortium. |

Whoever takes the file from here takes on these terms with it: for the
Atlas's part, CC BY 4.0 and its credit.
