
import os
import logging
from typing import Optional, Iterable, Tuple, List

from funowl.converters.functional_converter import to_python
from rdflib import Graph, Namespace, OWL, RDFS, XSD, URIRef, Literal
from rdflib.namespace import DC, SKOS, RDF

# -------------------- logging --------------------
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("ofn2md_fixed")

# -------------------- namespaces --------------------
TN = Namespace("https://isotc204.org/25965/transport/transportnetwork#")
GENPROP = Namespace("https://standards.iso.org/iso-iec/5087/-1/ed-1/en/ontology/GenericProperties#")

# Annotation predicates we consider as "description"
DESC_PROPS = (DC.description, SKOS.definition, RDFS.comment)

# Annotation predicates we never list under "Other Annotations"
SKIP_IN_OTHER = set(DESC_PROPS) | {RDFS.label}

# -------------------- helpers --------------------

def qname(g: Graph, uri: URIRef) -> str:
    try:
        return g.namespace_manager.normalizeUri(uri)
    except Exception:
        return str(uri)

def get_label(g: Graph, c: URIRef) -> str:
    for _, _, lbl in g.triples((c, RDFS.label, None)):
        if isinstance(lbl, Literal):
            return str(lbl)
    # fallback to fragment
    return str(c).split('#')[-1]

def get_first_literal(g: Graph, subj: URIRef, preds: Iterable[URIRef]) -> Optional[str]:
    for p in preds:
        for _, _, lit in g.triples((subj, p, None)):
            if isinstance(lit, Literal):
                return str(lit)
    return None

def iter_annotations(g: Graph, subj: URIRef) -> Iterable[Tuple[str, str]]:
    """Yield (predicate, value) pairs for annotations as readable strings,
    excluding those in SKIP_IN_OTHER.
    """
    for p, o in g.predicate_objects(subj):
        # Only consider annotation-like triples
        if not isinstance(p, URIRef):
            continue
        if p in SKIP_IN_OTHER:
            continue
        # Only simple literal values for "Other Annotations"
        if isinstance(o, Literal):
            yield (qname(g, p), str(o))

def class_restrictions(g: Graph, c: URIRef) -> List[Tuple[str, str]]:
    """Very lightweight extraction of some OWL restrictions that tend to be used in the user's MD.
    This is intentionally simple (matching previous script behavior)."""
    rows = []

    # Gather subclass axioms of the form: c rdfs:subClassOf _:restriction
    for _, _, restr in g.triples((c, RDFS.subClassOf, None)):
        # AllValuesFrom
        avf_class = None
        on_prop = None
        for _, _, onp in g.triples((restr, OWL.onProperty, None)):
            if isinstance(onp, URIRef):
                on_prop = qname(g, onp)
        for _, _, tgt in g.triples((restr, OWL.allValuesFrom, None)):
            if isinstance(tgt, URIRef):
                avf_class = qname(g, tgt)
        if on_prop and avf_class:
            rows.append((on_prop, f"All values from {avf_class}"))
            continue

        # cardinalities (qualified & unqualified common patterns)
        for pred, label in [
            (OWL.cardinality, "Exact cardinality"),
            (OWL.minCardinality, "Min cardinality"),
            (OWL.maxCardinality, "Max cardinality"),
        ]:
            for _, _, card in g.triples((restr, pred, None)):
                if isinstance(card, Literal):
                    # Try qualified
                    qclass = None
                    for _, _, tgt in g.triples((restr, OWL.onClass, None)):
                        if isinstance(tgt, URIRef):
                            qclass = qname(g, tgt)
                            break
                    if qclass:
                        rows.append((on_prop or "", f"{label} {int(card)} {qclass}"))
                    else:
                        rows.append((on_prop or "", f"{label} {int(card)}"))
    return rows

def get_pattern(g: Graph, c: URIRef) -> Optional[str]:
    """Look for an xsd:string literal giving a pattern name via a custom property if it exists."""
    xsd_pattern = URIRef(str(XSD))  # placeholder to avoid NameError if user used a custom prop
    # In the original script, they likely had a dedicated predicate; since we don't know it,
    # return None. The rest of the script will simply skip the Pattern section.
    return None

# -------------------- main --------------------

def main():
    # Input/Output
    input_ofn = os.environ.get("OFN_PATH", "transportnetwork.ofn")
    out_dir = os.environ.get("OUT_DIR", "md")
    diagrams_dir = os.environ.get("DIAGRAMS_DIR", "diagrams")

    # Load ontology (Functional Syntax via funowl) and convert to RDF
    try:
        with open(input_ofn, "r", encoding="utf-8") as f:
            onto = to_python(f)
    except FileNotFoundError:
        log.error(f"Error: {input_ofn} file not found")
        return 1
    except Exception as e:
        log.error(f"Error loading ontology: {e}")
        return 1

    g = Graph()
    try:
        onto.to_rdf(g)
    except Exception as e:
        log.error(f"Error converting ontology to RDF: {e}")
        return 1

    # Ensure output folder
    os.makedirs(out_dir, exist_ok=True)

    # Iterate classes in graph
    classes = set()
    for c in g.subjects(RDF.type, OWL.Class):
        classes.add(c)
    log.info("Found %d classes", len(classes))

    for c in sorted(classes, key=lambda u: get_label(g, u).lower()):
        try:
            cls_name = get_label(g, c)
            filename = os.path.join(out_dir, f"{cls_name}.md")
            log.debug("Writing %s", filename)

            # 1) Title
            title = f"# {cls_name}\n\n"

            # 2) Description (TOP) — pull from dc:description, skos:definition, or rdfs:comment
            desc = get_first_literal(g, c, DESC_PROPS)
            if desc:
                top_desc = f"{desc}\n\n"
            else:
                top_desc = ""

            # 3) Diagram link
            diagram_line = f"![{cls_name} Diagram](../{diagrams_dir}/{cls_name}.svg)\n\n"

            # 4) Restrictions table (if any)
            restr_rows = class_restrictions(g, c)
            restrictions_md = ""
            if restr_rows:
                restrictions_md += "## Restrictions\n\n"
                restrictions_md += "| Property | Restriction Type |\n"
                restrictions_md += "|----------|------------------|\n"
                for prop, restr in sorted(restr_rows):
                    restrictions_md += f"| {prop} | {restr} |\n"
                restrictions_md += "\n"

            # 5) Other Annotations — EXCLUDING the description properties
            other_ann = list(iter_annotations(g, c))
            other_md = ""
            if other_ann:
                other_md += "## Other Annotations\n\n"
                for p, v in sorted(other_ann):
                    other_md += f"- **{p}**: {v}\n"
                other_md += "\n"

            # 6) Pattern (if we can resolve one; noop by default here)
            pattern_name = get_pattern(g, c)
            pattern_md = ""
            if pattern_name:
                pattern_md = f"## Pattern\n\nThis class is a part of the [{pattern_name}](../{pattern_name}.md) pattern.\n\n"

            # Write the file
            with open(filename, "w", encoding="utf-8") as f:
                f.write(title)
                f.write(top_desc)      # <-- Description placed at the very top
                f.write(diagram_line)
                f.write(restrictions_md)
                f.write(other_md)
                f.write(pattern_md)

        except Exception as e:
            log.error("Error writing file for %s: %s", cls_name if 'cls_name' in locals() else c, e)
            continue

    log.info("Markdown files generated successfully.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
