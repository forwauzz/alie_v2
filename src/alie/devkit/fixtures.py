"""Synthetic fixtures (PRD §13.3), each with an expected page map in the shape of the
CNESST framework's Appendix B.

| Fixture | Exercises                                                      |
|---------|----------------------------------------------------------------|
| `tiny`  | happy path, seconds; printed label != pdf index                 |
| `hard`  | illegible, undated, ambiguous date, orphan page, OCR damage     |
| `dupes` | two bundles: refax, byte-identical pair, later-visit pair       |
| `admin` | filters: billing, consent, zero-content admin, clinical control |

`gold-cnesst` is not in the repo; it points at real files (§13.3).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import SETTINGS
from .pdfgen import Page, write_pdf

SIG = "Signé: "


def _tiny() -> list[Page]:
    return [
        Page(
            printed_label="1 de 1",
            lines=[
                "# RAPPORT MÉDICAL",
                "Commission des normes, de l'équité, de la santé et de la sécurité du travail",
                "Formulaire 1918 (2011-11)",
                "Nom du travailleur: TREMBLAY, Jean",
                "Date de naissance: 1974-08-21",
                "Date de l'événement: 2022-03-14",
                "Date de l'examen: 2022-04-02",
                "",
                "## DIAGNOSTIC",
                "Entorse lombaire",
                "",
                "## CONSOLIDATION",
                "[ ] Oui    [x] Non",
                "",
                SIG + "Dre Marie Lavoie, méd. de famille",
            ],
        ),
        Page(
            printed_label="p. 1 de 2",
            lines=[
                "# RAPPORT DE PHYSIOTHÉRAPIE",
                "Clinique PhysioSanté",
                "Date de la séance: 2022-05-10",
                "Travailleur: TREMBLAY, Jean",
                "",
                "## ÉVALUATION",
                "Amplitude lombaire limitée en flexion.",
                "Douleur 6/10 à la palpation L4-L5.",
            ],
        ),
        Page(
            printed_label="p. 2 de 2",
            lines=[
                "## PLAN DE TRAITEMENT",
                "Physiothérapie 2x/semaine, 6 semaines.",
                "Réévaluation prévue au terme du bloc.",
                "",
                SIG + "Sophie Girard, pht",
            ],
        ),
        Page(
            printed_label="1",
            lines=[
                "# RAPPORT D'IMAGERIE",
                "Centre d'imagerie médicale du Québec",
                "Examen: IRM colonne lombaire",
                "Date de l'examen: 2022-06-01",
                "",
                "## CONCLUSION",
                "Hernie discale L4-L5 avec contact radiculaire.",
                "",
                SIG + "Dr Alain Roy, radiologiste",
            ],
        ),
        # printed label 44 on pdf page 5 — the `Clinique mère et monde` case from §8.1.
        Page(
            printed_label="44",
            lines=[
                "# NOTE DE CONSULTATION",
                "Clinique médicale Mère et Monde",
                "Date de la visite: 2022-06-15",
                "",
                "## SUBJECTIF",
                "Persistance des lombalgies malgré la physiothérapie.",
                "",
                "## PLAN",
                "Référence en physiatrie.",
                "",
                SIG + "Dre Marie Lavoie",
            ],
        ),
    ]


def _hard() -> list[Page]:
    return [
        # OCR damage in headings, two-digit years, a mis-OCR'd percentage (§4.4, §4.3).
        Page(
            printed_label="1 de 1",
            lines=[
                "# RAPPORI M AL",
                "SANTÉ ET SÉCURIÉ DU TRAVAIL",
                "Formulaire 2064 (2012-06)",
                "Date de l'événement: 90-05-08",
                "Date de l'examen: 92-12-10",
                "",
                "## BILAN DES SÉQUELLES",
                "Code 102 383    2 %",
                "Code 204 219    2°2 %",
                "",
                "[x] Oui   Atteinte permanente reconnue",
                "",
                SIG + "Dr Pierre Bouchard, orthopédiste",
            ],
        ),
        # Opens here, continues at pdf page 5 — a page set, not a range (§8.3).
        Page(
            printed_label="p. 1 de 2",
            lines=[
                "# NOTE DE CONSULTATION",
                "Clinique du Nord",
                "Date de la visite: 2023-08-03",
                "",
                "## SUBJECTIF",
                "Douleur cervicale persistante, irradiation au membre supérieur droit.",
            ],
        ),
        Page(
            printed_label="1 de 2",
            lines=[
                "# RAPPORTD'IMAGERIE",
                "Examen: IRM Rachis Cervical",
                "Date de l'examen: 2023-08-01",
            ],
        ),
        Page(
            printed_label="2 de 2",
            lines=[
                "## CONCLUSION",
                "Discopathie dégénérative C5-C6 avec sténose foraminale.",
                "",
                SIG + "Dr Alain Roy, radiologiste",
            ],
        ),
        Page(
            printed_label="p. 2 de 2",
            lines=[
                "## PLAN",
                "Infiltration facettaire C5-C6.",
                "",
                SIG + "Dre Claire Fontaine",
            ],
        ),
        # 02-03-04 has more than one plausible reading. Ambiguity is a value (§8.4).
        Page(
            printed_label="1 de 1",
            lines=[
                "# CERTIFICAT MÉDICAL",
                "Date de l'examen: 02-03-04",
                "Diagnostic: Cervicalgie post-traumatique",
                "",
                SIG + "Dre Claire Fontaine",
            ],
        ),
        # No date anywhere. Leads the document under SANS DATE (§8.5).
        Page(
            lines=[
                "# RÉSULTAT DE LABORATOIRE",
                "Hémogramme complet",
                "Valeurs dans les limites de la normale.",
            ],
        ),
        # No text layer at all. Never sent to the model (§8.5).
        Page(image_only=True),
    ]


_CONSULT_2024_02_11 = [
    "# NOTE DE CONSULTATION",
    "Clinique médicale Saint-Laurent",
    "Date de la visite: 2024-02-11",
    "",
    "## SUBJECTIF",
    "Lombalgie mécanique, amélioration partielle.",
    "",
    SIG + "Dr Alain Roy",
]

_IRM_2024_03_05 = [
    "# RAPPORT D'IMAGERIE",
    "Examen: IRM colonne lombaire",
    "Date de l'examen: 2024-03-05",
    "",
    "## CONCLUSION",
    "Protrusion discale L5-S1.",
    "",
    SIG + "Dre Hélène Caron, radiologiste",
]


def _dupes_medical() -> list[Page]:
    return [
        Page(printed_label="1 de 1", lines=list(_CONSULT_2024_02_11)),
        Page(printed_label="1 de 1", lines=list(_IRM_2024_03_05)),
        Page(
            printed_label="1 de 1",
            lines=[
                "# NOTE DE CONSULTATION",
                "Clinique médicale Saint-Laurent",
                "Date de la visite: 2024-04-18",
                "",
                "## SUBJECTIF",
                "Reprise des activités progressives.",
                "",
                SIG + "Dr Alain Roy",
            ],
        ),
    ]


def _dupes_chum() -> list[Page]:
    return [
        # Same document, different artifact: a refax. Content identical, transmission axis
        # differs. Not removable — firm policy (§10.1).
        Page(
            printed_label="1 de 1",
            fax_banner="DE: CLINIQUE ST-LAURENT   05/07/2024 14:22   P.001",
            lines=list(_CONSULT_2024_02_11),
        ),
        # All seven axes match. The only auto-removable case (§10.1).
        Page(printed_label="1 de 1", lines=list(_IRM_2024_03_05)),
        # Amélie's example: identical masthead, different visit date. Keep both (§10.1).
        Page(
            printed_label="1 de 1",
            lines=[
                "# NOTE DE CONSULTATION",
                "Clinique médicale Saint-Laurent",
                "Date de la visite: 2024-05-20",
                "",
                "## SUBJECTIF",
                "Douleur résiduelle à l'effort.",
                "",
                SIG + "Dr Alain Roy",
            ],
        ),
    ]


#: Expected page map per fixture, in the shape of Appendix B. Tests assert the manifest
#: reproduces these spans and dates — the §14.1 proof, with no model having run.
def _admin() -> list[Page]:
    """Filters (§6). Admin noise excluded *by rule*, with a clinical control that must
    survive — a filter that removes a consultation note is worse than no filter at all.

    Nothing here is dropped: excluded units still reach the manifest with `excluded_by`
    naming the rule (§3.4).
    """
    return [
        Page(
            printed_label="1",
            lines=[
                "# FACTURE",
                "Clinique médicale Mère et Monde",
                "Numéro de facturation: 44921-03",
                "Date: 2022-07-04",
                "",
                "Consultation ............ 95,00 $",
                "Total ................... 95,00 $",
            ],
        ),
        Page(
            printed_label="2",
            lines=[
                "# FORMULAIRE DE CONSENTEMENT",
                "Autorisation de divulgation de renseignements médicaux",
                "Je, TREMBLAY, Jean, autorise la transmission de mon dossier.",
                "Date: 2022-07-05",
                "",
                SIG + "Jean Tremblay",
            ],
        ),
        # The control. Same bundle, same patient, genuinely clinical — must survive every
        # rule above it.
        Page(
            printed_label="3",
            lines=[
                "# NOTE DE CONSULTATION",
                "Clinique du Nord",
                "Date de la visite: 2022-07-12",
                "",
                "## SUBJECTIF",
                "Lombalgie persistante, irradiation au membre inférieur droit.",
                "",
                "## OBJECTIF",
                "Lasègue droit positif à 30 degrés.",
                "",
                SIG + "Dre Marie Lavoie",
            ],
        ),
        # Admin class, no clinical content: a title and a stamp. Excluded by rule; a
        # clinical document this empty would be kept as a title-only row (§8.5).
        Page(
            printed_label="4",
            lines=[
                "# REÇU",
                "Reçu pour dépôt au dossier.",
            ],
        ),
    ]


EXPECTED: dict[str, dict] = {
    "tiny": {
        "bundles": {"Médical": "Medical.pdf"},
        "units": [
            {"pages": [1], "class": "rapport_medical", "row_date": "2022-04-02"},
            {"pages": [2, 3], "class": "rapport_physiotherapie", "row_date": "2022-05-10"},
            {"pages": [4], "class": "rapport_imagerie", "row_date": "2022-06-01"},
            {"pages": [5], "class": "note_consultation", "row_date": "2022-06-15"},
        ],
        "printed_labels": {1: "1 de 1", 2: "p. 1 de 2", 3: "p. 2 de 2", 4: "1", 5: "44"},
    },
    "hard": {
        "bundles": {"Médical": "Medical.pdf"},
        "units": [
            {"pages": [1], "class": "rapport_evaluation_medicale", "row_date": "1992-12-10"},
            # The orphan re-join: non-contiguous, wrapping around the IRM (§8.3).
            {"pages": [2, 5], "class": "note_consultation", "row_date": "2023-08-03"},
            {"pages": [3, 4], "class": "rapport_imagerie", "row_date": "2023-08-01"},
            {"pages": [6], "class": "certificat_medical", "row_date": None, "status": "ambiguous"},
            {"pages": [7], "class": "resultat_laboratoire", "row_date": None, "status": "undated"},
            {"pages": [8], "class": "unknown", "row_date": None, "status": "illegible"},
        ],
    },
    "admin": {
        "bundles": {"Médical": "Medical.pdf"},
        "units": [
            {"pages": [1], "class": "administratif", "excluded_by": "cnesst.filter.billing"},
            {"pages": [2], "class": "administratif", "excluded_by": "cnesst.filter.consent"},
            # The control: clinical, kept, dated.
            {"pages": [3], "class": "note_consultation", "row_date": "2022-07-12"},
            {
                "pages": [4],
                "class": "administratif",
                "excluded_by": "cnesst.filter.zero_content_admin",
            },
        ],
    },
    "dupes": {
        "bundles": {"Médical": "Medical.pdf", "CHUM": "CHUM.pdf"},
        "pairs": [
            {"a": ["Médical", 1], "b": ["CHUM", 1], "verdict": "same_doc_different_artifact"},
            {"a": ["Médical", 2], "b": ["CHUM", 2], "verdict": "identical"},
            {"a": ["Médical", 3], "b": ["CHUM", 3], "verdict": "different"},
        ],
    },
}

_BUILDERS = {
    ("tiny", "Medical.pdf"): _tiny,
    ("hard", "Medical.pdf"): _hard,
    ("admin", "Medical.pdf"): _admin,
    ("dupes", "Medical.pdf"): _dupes_medical,
    ("dupes", "CHUM.pdf"): _dupes_chum,
}


def build(root: Path | None = None) -> list[Path]:
    """Write every fixture PDF and its expected page map. Idempotent."""
    root = root or SETTINGS.fixtures_dir
    written: list[Path] = []
    for (name, filename), builder in _BUILDERS.items():
        written.append(write_pdf(root / name / filename, builder()))
    for name, expected in EXPECTED.items():
        path = root / name / "expected.json"
        path.write_text(
            json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def fixture_path(name: str, filename: str, root: Path | None = None) -> Path:
    return (root or SETTINGS.fixtures_dir) / name / filename
